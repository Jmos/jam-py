import sys
import json
import traceback

import jam.common as common
import jam.db.db_modules as db_modules
from jam.dataset import *

class SQL(object):

    def get_next_id(self, db_module=None):
        if db_module is None:
            db_module = self.task.db_module
        sql = db_module.next_sequence_value_sql(self.gen_name)
        if sql:
            rec = self.task.execute_select(sql)
            if rec:
                if rec[0][0]:
                    return int(rec[0][0])

    def insert_sql(self, db_module):
        if self._deleted_flag:
            self._deleted_flag_field.set_data(0)
        row = []
        fields = []
        values = []
        index = 0
        for field in self.fields:
            if not (field.calculated or field.master_field):
                index += 1
                fields.append('"%s"' % field.db_field_name)
                values.append('%s' % db_module.value_literal(index))
                value = (field.raw_value, field.data_type)
                row.append(value)
        fields = ', '.join(fields)
        values = ', '.join(values)
        sql = 'INSERT INTO "%s" (%s) VALUES (%s)' % \
            (self.table_name, fields, values)
        return sql, row

    def update_sql(self, db_module):
        row = []
        command = 'UPDATE "%s" SET ' % self.table_name
        fields = []
        index = 0
        for field in self.fields:
            if not (field.calculated or field.master_field):
                index += 1
                fields.append('"%s"=%s' % (field.db_field_name, db_module.value_literal(index)))
                value = (field.raw_value, field.data_type)
                if field.field_name == self._deleted_flag:
                    value = (0, field.data_type)
                row.append(value)
        fields = ', '.join(fields)
        if self._primary_key_field.data_type == common.TEXT:
            where = " WHERE %s = '%s'" % (self._primary_key_db_field_name, self._primary_key_field.value)
        else:
            where = ' WHERE %s = %s' % (self._primary_key_db_field_name, self._primary_key_field.value)
        return ''.join([command, fields, where]), row

    def delete_sql(self, db_module):
        soft_delete = self.soft_delete
        if self.master:
            soft_delete = self.master.soft_delete
        if soft_delete:
            sql = 'UPDATE %s SET %s = 1 WHERE %s = %s' % \
                (self.table_name, self._deleted_flag_db_field_name,
                self._primary_key_db_field_name, self._primary_key_field.value)
        else:
            sql = 'DELETE FROM %s WHERE %s = %s' % \
                (self.table_name, self._primary_key_db_field_name,
                self._primary_key_field.value)
        return sql

    def apply_sql(self, safe=False, db_module=None):

        def get_sql(item, safe, db_module):
            if item.master:
                if item._master_id:
                    item._master_id_field.set_data(item.master.ID)
                item._master_rec_id_field.set_data(item.master._primary_key_field.value)
            if item.record_status == common.RECORD_INSERTED:
                if safe and not self.can_create():
                    raise Exception(self.task.lang['cant_create'] % self.item_caption)
                sql, param = item.insert_sql(db_module)
            elif item.record_status == common.RECORD_MODIFIED:
                if safe and not self.can_edit():
                    raise Exception(self.task.lang['cant_edit'] % self.item_caption)
                sql, param = item.update_sql(db_module)
            elif item.record_status == common.RECORD_DETAILS_MODIFIED:
                sql, param = '', None
            elif item.record_status == common.RECORD_DELETED:
                if safe and not self.can_delete():
                    raise Exception(self.task.lang['cant_delete'] % self.item_caption)
                sql = item.delete_sql(db_module)
                param = None
            else:
                raise Exception('apply_sql - invalid %s record_status %s, record: %s' % \
                    (item.item_name, item.record_status, item._dataset[item.rec_no]))
            primary_key_index = item.fields.index(item._primary_key_field)
            master_rec_id_index = None
            if item.master:
                master_rec_id_index = item.fields.index(item._master_rec_id_field)
            info = {
                'ID': item.ID,
                'gen_name': item.gen_name,
                'status': item.record_status,
                'primary_key': item._primary_key_field.value,
                'primary_key_type': item._primary_key_field.data_type,
                'primary_key_index': primary_key_index,
                'log_id': item.get_rec_info()[common.REC_CHANGE_ID],
                'master_rec_id_index': master_rec_id_index
                }
            h_sql, h_params, h_gen_name = get_history_sql(item, db_module)
            return sql, param, info, h_sql, h_params, h_gen_name

        def delete_detail_sql(item, detail, db_module):
            if detail._master_id:
                if item.soft_delete:
                    sql = 'UPDATE %s SET %s = 1 WHERE %s = %s AND %s = %s' % \
                        (detail.table_name, detail._deleted_flag_db_field_name, detail._master_id_db_field_name, \
                        item.ID, detail._master_rec_id_db_field_name, self._primary_key_field.value)
                else:
                    sql = 'DELETE FROM %s WHERE %s = %s AND %s = %s' % \
                        (detail.table_name, detail._master_id_db_field_name, item.ID, \
                        detail._master_rec_id_db_field_name, self._primary_key_field.value)
            else:
                if item.soft_delete:
                    sql = 'UPDATE %s SET %s = 1 WHERE %s = %s' % \
                        (detail.table_name, detail._deleted_flag_db_field_name, \
                        detail._master_rec_id_db_field_name, self._primary_key_field.value)
                else:
                    sql = 'DELETE FROM %s WHERE %s = %s' % \
                        (detail.table_name, detail._master_rec_id_db_field_name, \
                        self._primary_key_field.value)
            h_sql, h_params, h_gen_name = get_history_sql(item, db_module)
            return sql, None, None, h_sql, h_params, h_gen_name

        def get_history_sql(item, db_module):
            h_sql = None
            h_params = None
            h_gen_name = None
            user_info = None
            if item.session:
                user_info = item.session.get('user_info')
            if item.task.history_item and item.keep_history:
                h_gen_name = item.task.history_item.gen_name
                changes = None
                user = None
                if user_info:
                    try:
                        user = user_info['user_name']
                    except:
                        pass
                if item.record_status != common.RECORD_DELETED:
                    old_rec = item.get_rec_info()[3]
                    new_rec = item._dataset[item.rec_no]
                    f_list = []
                    for f in item.fields:
                        old = None
                        if old_rec:
                            old = old_rec[f.bind_index]
                        new = new_rec[f.bind_index]
                        if old != new:
                            f_list.append([f.ID, old, new])
                    d_list = []
                    if not item.master:
                        for detail in item.details:
                            for d in detail:
                                d_list.append([d.ID, d._primary_key_field.value, d.record_status])
                    changes = (json.dumps([f_list, d_list], default=common.json_defaul_handler), common.BLOB)
                if not hasattr(item.task, '__history_fields'):
                    h_fields = ['id', 'item_id', 'item_rec_id', 'operation', 'changes', 'user', 'date']
                    if item.task.history_item._deleted_flag:
                        h_fields.append('deleted')
                    item.task.__history_fields = []
                    for f in h_fields:
                        item.task.__history_fields.append(item.task.history_item._field_by_name(f))
                h_params = [None, item.ID, item._primary_key_field.value, item.record_status, changes, user, datetime.datetime.now()]
                if item.task.history_item._deleted_flag:
                    h_params.append(0)
                index = 0
                fields = ''
                values = ''
                for f in item.task.__history_fields:
                    index += 1
                    fields += '"%s", ' % f.db_field_name
                    values +=  '%s, ' % db_module.value_literal(index)
                fields = fields[:-2]
                values = values[:-2]
                h_sql = 'INSERT INTO "%s" (%s) VALUES (%s)' % \
                    (item.task.history_item.table_name, fields, values)
            return h_sql, h_params, h_gen_name

        def generate_sql(item, safe, db_module, result):
            ID, sql = result
            for it in item:
                details = []
                sql.append((get_sql(it, safe, db_module), details))
                for detail in item.details:
                    detail_sql = []
                    detail_result = (str(detail.ID), detail_sql)
                    details.append(detail_result)
                    if item.record_status == common.RECORD_DELETED:
                        detail_sql.append((delete_detail_sql(item, detail, db_module), []))
                    else:
                        generate_sql(detail, safe, db_module, detail_result)

        if db_module is None:
            db_module = self.task.db_module
        result = (self.ID, [])
        generate_sql(self, safe, db_module, result)
        return {'delta': result}

    def table_alias(self):
        return '"%s"' % self.table_name

    def lookup_table_alias(self, field):
        if field.master_field:
            return '%s_%d' % (field.lookup_item.table_name, field.master_field.ID)
        else:
            return '%s_%d' % (field.lookup_item.table_name, field.ID)

    def lookup_table_alias1(self, field):
        return self.lookup_table_alias(field) + '_' + field.lookup_db_field

    def lookup_table_alias2(self, field):
        return self.lookup_table_alias1(field) + '_' + field.lookup_db_field1

    def fields_clause(self, query, fields, db_module=None):
        if db_module is None:
            db_module = self.task.db_module
        funcs = query.get('__funcs')
        if funcs:
            functions = {}
            for key, value in funcs.iteritems():
                functions[key.upper()] = value
        sql = []
        for field in fields:
            if field.master_field:
                pass
            elif field.calculated:
                sql.append('NULL AS "%s"' % field.db_field_name)
            else:
                field_sql = '%s."%s"' % (self.table_alias(), field.db_field_name)
                if funcs:
                    func = functions.get(field.field_name.upper())
                    if func:
                        field_sql = '%s(%s) AS "%s"' % (func.upper(), field_sql, field.db_field_name)
                sql.append(field_sql)
        if query['__expanded']:
            for field in fields:
                if field.lookup_item:
                    if field.lookup_field2:
                        field_sql = '%s."%s" %s %s_LOOKUP' % \
                        (self.lookup_table_alias2(field), field.lookup_db_field2, db_module.FIELD_AS, field.db_field_name)
                        if funcs:
                            func = functions.get(field.field_name.upper())
                            if func:
                                field_sql = '%s(%s."%s") %s %s_LOOKUP' % \
                                (func.upper(), self.lookup_table_alias2(field), field.lookup_db_field2, db_module.FIELD_AS, field.db_field_name)
                    elif field.lookup_field1:
                        field_sql = '%s."%s" %s %s_LOOKUP' % \
                        (self.lookup_table_alias1(field), field.lookup_db_field1, db_module.FIELD_AS, field.db_field_name)
                        if funcs:
                            func = functions.get(field.field_name.upper())
                            if func:
                                field_sql = '%s(%s."%s") %s %s_LOOKUP' % \
                                (func.upper(), self.lookup_table_alias1(field), field.lookup_db_field1, db_module.FIELD_AS, field.db_field_name)
                    else:
                        field_sql = '%s."%s" %s %s_LOOKUP' % \
                        (self.lookup_table_alias(field), field.lookup_db_field, db_module.FIELD_AS, field.db_field_name)
                        if funcs:
                            func = functions.get(field.field_name.upper())
                            if func:
                                field_sql = '%s(%s."%s") %s %s_LOOKUP' % \
                                (func.upper(), self.lookup_table_alias(field), field.lookup_db_field, db_module.FIELD_AS, field.db_field_name)
                    sql.append(field_sql)
        sql = ', '.join(sql)
        return sql

    def from_clause(self, query, fields, db_module=None):
        if db_module is None:
            db_module = self.task.db_module
        result = []
        result.append(db_module.FROM % (self.table_name, self.table_alias()))
        if query['__expanded']:
            joins = {}
            for field in fields:
                if field.lookup_item:
                    alias = self.lookup_table_alias(field)
                    cur_field = field
                    if field.master_field:
                        cur_field = field.master_field
                    if not joins.get(alias):
                        primary_key_field_name = field.lookup_item._primary_key_db_field_name
                        result.append('%s ON %s."%s" = %s."%s"' % (
                            db_module.LEFT_OUTER_JOIN % (field.lookup_item.table_name, self.lookup_table_alias(field)),
                            self.table_alias(),
                            cur_field.db_field_name,
                            self.lookup_table_alias(field),
                            primary_key_field_name
                        ))
                        joins[alias] = True
                if field.lookup_item1:
                    alias = self.lookup_table_alias1(field)
                    if not joins.get(alias):
                        primary_key_field_name = field.lookup_item1._primary_key_db_field_name
                        result.append('%s ON %s."%s" = %s."%s"' % (
                            db_module.LEFT_OUTER_JOIN % (field.lookup_item1.table_name, self.lookup_table_alias1(field)),
                            self.lookup_table_alias(field),
                            field.lookup_db_field,
                            self.lookup_table_alias1(field),
                            primary_key_field_name
                        ))
                        joins[alias] = True
                if field.lookup_item2:
                    alias = self.lookup_table_alias2(field)
                    if not joins.get(alias):
                        primary_key_field_name = field.lookup_item2._primary_key_db_field_name
                        result.append('%s ON %s."%s" = %s."%s"' % (
                            db_module.LEFT_OUTER_JOIN % (field.lookup_item2.table_name, self.lookup_table_alias2(field)),
                            self.lookup_table_alias1(field),
                            field.lookup_db_field1,
                            self.lookup_table_alias2(field),
                            primary_key_field_name
                        ))
                        joins[alias] = True
        return ' '.join(result)

    def where_clause(self, query, db_module=None):

        def get_filter_sign(filter_type, value=None):
            result = common.FILTER_SIGN[filter_type]
            if filter_type == common.FILTER_ISNULL:
                if value:
                    result = 'IS NULL'
                else:
                    result = 'IS NOT NULL'
            if (result == 'LIKE'):
                result = db_module.LIKE
            return result

        def convert_field_value(field, value, filter_type=None):
            data_type = field.data_type
            if data_type == common.DATE:
                if type(value) in (str, unicode):
                    result = value
                else:
                    result = value.strftime('%Y-%m-%d')
                return db_module.cast_date(result)
            elif data_type == common.DATETIME:
                if type(value) in (str, unicode):
                    result = value
                else:
                    result = value.strftime('%Y-%m-%d %H:%M')
                result = db_module.cast_datetime(result)
                return result
            elif data_type == common.INTEGER:
                if type(value) == int or type(value) in [str, unicode] and value.isdigit():
                    return str(value)
                else:
                    if filter_type and filter_type in [common.FILTER_CONTAINS, common.FILTER_STARTWITH, common.FILTER_ENDWITH]:
                        return value
                    else:
                        return "'" + value + "'"
            elif data_type == common.BOOLEAN:
                if value:
                    return '1'
                else:
                    return '0'
            elif data_type == common.TEXT:
                if filter_type and filter_type in [common.FILTER_CONTAINS, common.FILTER_STARTWITH, common.FILTER_ENDWITH]:
                    return value
                else:
                    return "'" + value + "'"
            elif data_type in (common.FLOAT, common.CURRENCY):
                value = float(value)
                return str(value)
            else:
                return value

        def escape_search(value, esc_char):
            result = ''
            found = False
            for ch in value:
                if ch == "'":
                    ch = ch + ch
                elif ch in ['_', '%']:
                    ch = esc_char + ch
                    found = True
                result += ch
            return result, found

        def get_condition(field, filter_type, value):
            esc_char = '/'
            cond_field_name = '%s."%s"' % (self.table_alias(), field.db_field_name)
            if type(value) == str:
                value = value.decode('utf-8')
            filter_sign = get_filter_sign(filter_type, value)
            cond_string = '%s %s %s'
            if filter_type in (common.FILTER_IN, common.FILTER_NOT_IN):
                lst = '('
                if len(value):
                    for it in value:
                        lst += convert_field_value(field, it) + ', '
                    value = lst[:-2] + ')'
                else:
                    value = '()'
            elif filter_type == common.FILTER_RANGE:
                value = convert_field_value(field, value[0]) + \
                    ' AND ' + convert_field_value(field, value[1])
            elif filter_type == common.FILTER_ISNULL:
                value = ''
            else:
                value = convert_field_value(field, value, filter_type)
                if filter_type in [common.FILTER_CONTAINS, common.FILTER_STARTWITH, common.FILTER_ENDWITH]:
                    value, esc_found = escape_search(value, esc_char)
                    if field.lookup_item:
                        if field.lookup_item1:
                            cond_field_name = '%s."%s"' % (self.lookup_table_alias1(field), field.lookup_field1)
                        else:
                            cond_field_name = '%s."%s"' % (self.lookup_table_alias(field), field.lookup_field)
                    if filter_type == common.FILTER_CONTAINS:
                        value = '%' + value + '%'
                    elif filter_type == common.FILTER_STARTWITH:
                        value = value + '%'
                    elif filter_type == common.FILTER_ENDWITH:
                        value = '%' + value
                    upper_function =  db_module.upper_function()
                    if upper_function:
                        cond_string = upper_function + '(%s) %s %s'
                        value = value.upper()
                    if esc_found:
                        value = "'" + value + "' ESCAPE '" + esc_char + "'"
                    else:
                        value = "'" + value + "'"
            sql = cond_string % (cond_field_name, filter_sign, value)
            if field.data_type == common.BOOLEAN and value == '0':
                if filter_sign == '=':
                    sql = '(' + sql + ' OR %s IS NULL)' % cond_field_name
                elif filter_sign == '<>':
                    sql = '(' + sql + ' AND %s IS NOT NULL)' % cond_field_name
                else:
                    raise Exception('sql.py where_clause method: boolen field condition may give ambiguious results.')
            return sql

        if db_module is None:
            db_module = self.task.db_module
        conditions = []
        filters = query['__filters']
        deleted_in_filters = False
        if filters:
            for (field_name, filter_type, value) in filters:
                if not value is None:
                    field = self._field_by_name(field_name)
                    if field_name == self._deleted_flag:
                        deleted_in_filters = True
                    if filter_type == common.FILTER_CONTAINS_ALL:
                        values = value.split()
                        for val in values:
                            conditions.append(get_condition(field, common.FILTER_CONTAINS, val))
                    elif filter_type in [common.FILTER_IN, common.FILTER_NOT_IN] and \
                        type(value) in [tuple, list] and len(value) == 0:
                        conditions.append('%s."%s" IN (NULL)' % (self.table_alias(), self._primary_key_db_field_name))
                    else:
                        conditions.append(get_condition(field, filter_type, value))
        if not deleted_in_filters and self._deleted_flag:
            conditions.append('%s."%s"=0' % (self.table_alias(), self._deleted_flag_db_field_name))
        result = ' AND '.join(conditions)
        if result:
            result = ' WHERE ' + result
        return result

    def group_clause(self, query, fields, db_module=None):
        if db_module is None:
            db_module = self.task.db_module
        group_fields = query.get('__group_by')
        result = ''
        if group_fields:
            for field_name in group_fields:
                field = self._field_by_name(field_name)
                result += '%s."%s", ' % (self.table_alias(), field_name)
                if field.lookup_item:
                    result += '%s_LOOKUP, ' % field.db_field_name
            if result:
                result = result[:-2]
                result = ' GROUP BY ' + result
            return result
        else:
            return ''

    def order_clause(self, query, db_module=None):
        if query.get('__funcs') and not query.get('__group_by'):
            return ''
        if db_module is None:
            db_module = self.task.db_module
        order_list = query.get('__order', [])
        orders = []
        for order in order_list:
            field = self._field_by_ID(order[0])
            if field:
                if query['__expanded'] and field.lookup_item:
                    ord_str = '%s_LOOKUP' % field.db_field_name
                else:
                    ord_str = '%s."%s"' % (self.table_alias(), field.db_field_name)
                if order[1]:
                    ord_str += ' DESC'
                orders.append(ord_str)
        if orders:
             result = ' ORDER BY %s' % ', '.join(orders)
        else:
            result = ''
        return result

    def get_select_statement(self, query, db_module=None):
        try:
            if db_module is None:
                db_module = self.task.db_module
            field_list = query['__fields']
            if len(field_list):
                fields = [self._field_by_name(field_name) for field_name in field_list]
            else:
                fields = self._fields
            start = self.fields_clause(query, fields, db_module)
            end = ''.join([
                self.from_clause(query, fields, db_module),
                self.where_clause(query, db_module),
                self.group_clause(query, fields, db_module),
                self.order_clause(query, db_module)
            ])
            sql = db_module.get_select(query, start, end, fields)
            return sql
        except Exception as e:
            traceback.print_exc()
            raise

    def get_record_count_query(self, query, db_module=None):
        if db_module is None:
            db_module = self.task.db_module
        fields = []
        filters = query['__filters']
        if filters:
            for (field_name, filter_type, value) in filters:
                fields.append(self._field_by_name(field_name))
        sql = 'SELECT COUNT(*) FROM %s %s' % (self.from_clause(query, fields, db_module),
            self.where_clause(query, db_module))
        return sql

    def create_table_sql(self, db_type, table_name, fields=None, gen_name=None, foreign_fields=None):
        if not fields:
            fields = []
            for field in self.fields:
                if not (field.calculated or field.master_field):
                    dic = {}
                    dic['id'] = field.ID
                    dic['field_name'] = field.db_field_name
                    dic['data_type'] = field.data_type
                    dic['size'] = field.field_size
                    dic['default_value'] = field.f_default_value.value
                    dic['primary_key'] = field.id.value == item.f_primary_key.value
                    fields.append(dic)
        result = []
        db_module = db_modules.get_db_module(db_type)
        result = db_module.create_table_sql(table_name, fields, gen_name, foreign_fields)
        for i, s in enumerate(result):
            print(result[i])
        return result

    def delete_table_sql(self, db_type):
        db_module = db_modules.get_db_module(db_type)
        result = db_module.delete_table_sql(self.f_table_name.value, self.f_gen_name.value)
        for i, s in enumerate(result):
            print(result[i])
        return result

    def recreate_table_sql(self, db_type, old_fields, new_fields, fk_delta=None):

        def foreign_key_dict(ind):
            fields = ind.task.sys_fields.copy()
            fields.set_where(id=ind.f_foreign_field.value)
            fields.open()
            dic = {}
            dic['key'] = fields.f_db_field_name.value
            ref_id = fields.f_object.value
            items = self.task.sys_items.copy()
            items.set_where(id=ref_id)
            items.open()
            dic['ref'] = items.f_table_name.value
            primary_key = items.f_primary_key.value
            fields.set_where(id=primary_key)
            fields.open()
            dic['primary_key'] = fields.f_db_field_name.value
            return dic

        def get_foreign_fields():
            indices = self.task.sys_indices.copy()
            indices.filters.owner_rec_id.value = self.id.value
            indices.open()
            del_id = None
            if fk_delta and (fk_delta.rec_modified() or fk_delta.rec_deleted()):
                del_id = fk_delta.id.value
            result = []
            for ind in indices:
                if ind.f_foreign_index.value:
                    if not del_id or ind.id.value != del_id:
                        result.append(foreign_key_dict(ind))
            if fk_delta and (fk_delta.rec_inserted() or fk_delta.rec_modified()):
                result.append(foreign_key_dict(fk_delta))
            return result

        def create_indices_sql(db_type):
            indices = self.task.sys_indices.copy()
            indices.filters.owner_rec_id.value = self.id.value
            indices.open()
            result = []
            for ind in indices:
                if not ind.f_foreign_index.value:
                    result.append(ind.create_index_sql(db_type, self.f_table_name.value, new_fields=new_fields))
            return result

        def find_field(fields, id_value):
            found = False
            for f in fields:
                if f['id'] == id_value:
                    found = True
                    break
            return found

        def prepare_fields():
            for f in list(new_fields):
                if not find_field(old_fields, f['id']):
                    new_fields.remove(f)
            for f in list(old_fields):
                if not find_field(new_fields, f['id']):
                    old_fields.remove(f)

        table_name = self.f_table_name.value
        result = []
        result.append('ALTER TABLE "%s" RENAME TO Temp' % table_name)
        foreign_fields = get_foreign_fields()
        create_sql = self.create_table_sql(db_type, table_name, new_fields, foreign_fields)
        for sql in create_sql:
            result.append(sql)
        prepare_fields()
        old_field_list = ['"%s"' % field['field_name'] for field in old_fields]
        new_field_list = ['"%s"' % field['field_name'] for field in new_fields]
        result.append('INSERT INTO "%s" (%s) SELECT %s FROM Temp' % (table_name, ', '.join(new_field_list), ', '.join(old_field_list)))
        result.append('DROP TABLE Temp')
        ind_sql = create_indices_sql(db_type)
        for sql in ind_sql:
            result.append(sql)
        return result

    def change_table_sql(self, db_type, old_fields, new_fields):

        def recreate(comp):
            for key, (old_field, new_field) in comp.iteritems():
                if old_field and new_field:
                    if old_field['field_name'] != new_field['field_name']:
                        return True
                    elif old_field['default_value'] != new_field['default_value']:
                        return True
                elif old_field and not new_field:
                    return True

        db_module = db_modules.get_db_module(db_type)
        table_name = self.f_table_name.value
        result = []
        comp = {}
        for field in old_fields:
            comp[field['id']] = [field, None]
        for field in new_fields:
            if comp.get(field['id']):
                comp[field['id']][1] = field
            else:
                if field['id']:
                    comp[field['id']] = [None, field]
                else:
                    comp[field['field_name']] = [None, field]
        if db_type == db_modules.SQLITE and recreate(comp):
            result += self.recreate_table_sql(db_type, old_fields, new_fields)
        else:
            for key, (old_field, new_field) in comp.iteritems():
                if old_field and not new_field and db_type != db_modules.SQLITE:
                    result.append(db_module.del_field_sql(table_name, old_field))
            for key, (old_field, new_field) in comp.iteritems():
                if old_field and new_field and db_type != db_modules.SQLITE:
                    if (old_field['field_name'] != new_field['field_name']) or \
                        (db_module.FIELD_TYPES[old_field['data_type']] != db_module.FIELD_TYPES[new_field['data_type']]) or \
                        (old_field['default_value'] != new_field['default_value']) or \
                        (old_field['size'] != new_field['size']):
                        sql = db_module.change_field_sql(table_name, old_field, new_field)
                        if type(sql) in (list, tuple):
                            result += sql
                        else:
                            result.append()
            for key, (old_field, new_field) in comp.iteritems():
                if not old_field and new_field:
                    result.append(db_module.add_field_sql(table_name, new_field))
        for i, s in enumerate(result):
            print(result[i])
        return result

    def create_index_sql(self, db_type, table_name, fields=None, new_fields=None, foreign_key_dict=None):

        def new_field_name_by_id(id_value):
            for f in new_fields:
                if f['id'] == id_value:
                    return f['field_name']

        db_module = db_modules.get_db_module(db_type)
        index_name = self.f_index_name.value
        if self.f_foreign_index.value:
            if foreign_key_dict:
                key = foreign_key_dict['key']
                ref = foreign_key_dict['ref']
                primary_key = foreign_key_dict['primary_key']
            else:
                fields = self.task.sys_fields.copy()
                fields.set_where(id=self.f_foreign_field.value)
                fields.open()
                key = fields.f_db_field_name.value
                ref_id = fields.f_object.value
                items = self.task.sys_items.copy()
                items.set_where(id=ref_id)
                items.open()
                ref = items.f_table_name.value
                primary_key = items.f_primary_key.value
                fields.set_where(id=primary_key)
                fields.open()
                primary_key = fields.f_db_field_name.value
            sql = db_module.create_foreign_index_sql(table_name, index_name, key, ref, primary_key)
        else:
            index_fields = self.f_fields_list.value
            desc = ''
            if self.descending.value:
                desc = 'DESC'
            unique = ''
            if self.f_unique_index.value:
                unique = 'UNIQUE'
            fields = common.load_index_fields(index_fields)
            if db_type == db_modules.FIREBIRD:
                if new_fields:
                    field_defs = [new_field_name_by_id(field[0]) for field in fields]
                else:
                    field_defs = [self.task.sys_fields.field_by_id(field[0], 'f_field_name') for field in fields]
                field_str = '"' + '", "'.join(field_defs) + '"'
            else:
                field_defs = []
                for field in fields:
                    if new_fields:
                        field_name = new_field_name_by_id(field[0])
                    else:
                        field_name = self.task.sys_fields.field_by_id(field[0], 'f_field_name')
                    d = ''
                    if field[1]:
                        d = 'DESC'
                    field_defs.append('"%s" %s' % (field_name, d))
                field_str = ', '.join(field_defs)
            sql = db_module.create_index_sql(index_name, table_name, unique, field_str, desc)
        print(sql)
        return sql

    def delete_index_sql(self, db_type):
        db_module = db_modules.get_db_module(db_type)
        table_name = self.task.sys_items.field_by_id(self.owner_rec_id.value, 'f_table_name')
        index_name = self.f_index_name.value
        if self.f_foreign_index.value:
            sql = db_module.delete_foreign_index(table_name, index_name)
        else:
            sql = db_module.delete_index(table_name, index_name)
        print(sql)
        return sql
