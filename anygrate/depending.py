import xmlrpclib
import argparse
import logging
from os.path import basename
logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger(basename(__file__))


def main():
    """ Main console script
    """
    parser = argparse.ArgumentParser(description='Return the dependencies order'
                                     ' of models give as parameters')
    parser.add_argument('-m', '--models', nargs='+', help="One or many models",
                        required=True)
    parser.add_argument('-df', '--db_name_from',
                        help="Name of the database to migrate data from",
                        required=True)
    parser.add_argument('-dt', '--db_name_to',
                        help="Name of the database to migrate data to",
                        required=False)  # Temporary, will be required
    parser.add_argument('-ut', '--user_to',
                        help="Name of the user of the database aimed",
                        required=False)  # Temporay, will be required
    parser.add_argument('-uf', '--user_from',
                        help="Name of the user of the database source",
                        required=True)
    parser.add_argument('-pf', '--pwd_from',
                        help="Password of the user of the database source",
                        required=True)
    parser.add_argument('-pt', '--pwd_to',
                        help="Password of the user of the database aimed",
                        required=False)  # Temporary, will be required
    parser.add_argument('-x', '--excluded', nargs='+', help="One or many models"
                        " to exclude", required=False, default=None)
    #args = parser.parse_args()
    #username_from = args.user_from
    #username_to = args.user_to
    #pwd_from = args.pwd_from
    #pwd_to = args.pwd_to
    #dbname_from = args.db_name_from
    #dbname_to = args.db_name_to
    #models = args.models
    #excluded_models = args.excluded
    #ordered_models = get_dependencies(username_from,
    #                                       pwd_from,
    #                                       dbname_from,
    #                                       models,
    #                                       excluded_models)


if __name__ == '__main__':
    main()


def encapsulation_get_dep(username, pwd, dbname, models, excluded_models,
                          path=None, seen=None, related_tables=None):

    res, related_tables = get_dependencies(username, pwd, dbname, models,
                                           excluded_models)
    for tbl in related_tables:
        res.append(tbl)
    return res


def get_dependencies(username, pwd, dbname, models, excluded_models,
                     path=None, seen=None, related_tables=None):
    """ Given a list of OpenERP models, return the full list of dependant models,
    ordered by dependencies. Warning are displayed if there are dependency loops
    Set excluded_models to None if there is no model to exclude.
    If you want to exclude some models, use the following syntax :
    excluded_models = ['res.currency', 'res.country']
    """
    # XML-RPC
    sock, uid = get_socket(username, pwd, dbname, 8069)
    res = []
    if seen is None:
        seen = set()
    if path is None:
        path = ()
    if excluded_models is not None:
        for excl_model in excluded_models:
            seen.add(excl_model)
        excluded_models = None
    if related_tables is None:
        related_tables = set()
    for model in models:
        m2o = set()
        m2m = set()
        seen.add(model)
        fields = sock.execute(dbname, uid, pwd, model, 'fields_get')
        for field in fields:
            if fields[field]['type'] == 'many2one':
                m = fields[field]['relation']
                # Cas des structures arborescentes (reflexives)
                if m in path:
                    LOG.warn('Dependency LOOP: '
                             '%s has a m2o %r to %s which is one of its ancestors (path=%r)',
                             model, field, m, path)
                if m not in seen:
                    m2o.add(m)
                    seen.add(m)
            if fields[field]['type'] == 'many2many' and 'related_columns' in fields[field]:
                m = fields[field]['relation']
                third_table = fields[field]['third_table']
                # Cas des structures arborescentes (reflexives)
                if m in path:
                    LOG.warn('Dependency LOOP: '
                             '%s has a m2m %r to %s which is one of its ancestors (path=%r)',
                             model, field, m, path)
                if m not in seen:
                    m2m.add(m)
                    seen.add(m)
                if third_table not in seen:
                    seen.add(third_table)
                if third_table not in related_tables:
                    related_tables.add(third_table)
        for m in m2m:
            result, related_tables = get_dependencies(username, pwd, dbname, (m,),
                                    path=path+(model,),
                                    excluded_models=excluded_models,
                                    seen=seen, related_tables=related_tables)
            res += result
        for m in m2o:
            result, related_tables = get_dependencies(username, pwd, dbname, (m,),
                                    path=path+(model,),
                                    excluded_models=excluded_models,
                                    seen=seen, related_tables=related_tables)
            res += result
        if model == 'ir.actions.actions':
            model = 'ir.actions'
        if model not in res:
            res.append(model)
    return res, related_tables


def get_fk_to_update(connection, models):
    """ Method to get back all columns referencing another table
    """
    fields2update = {}
    for model in models:
        with connection.cursor() as c:
            if model not in fields2update:
                if model == 'ir.actions':
                    model = 'ir.actions.actions'
                model = model.replace('.', '_')
                query = """
SELECT tc.table_name, kcu.column_name
FROM information_schema.table_constraints AS tc JOIN
information_schema.key_column_usage AS kcu ON
tc.constraint_name = kcu.constraint_name JOIN
information_schema.constraint_column_usage AS
ccu ON ccu.constraint_name = tc.constraint_name
WHERE constraint_type = 'FOREIGN KEY' AND
ccu.table_name='%s';""" % model
                c.execute(query)
                results = c.fetchall()
                fields2update[model] = results
    # transpose the result to obtain:
    # {'table.fkname': 'pointed_table', ...}
    # so that processing each input line is easier
    result = {}
    for pointed_table, fknames in fields2update.iteritems():
        for fkname in fknames:
            result['.'.join(fkname)] = pointed_table
    return result


def get_mapping_migration(username_from, username_to, pwd_from, pwd_to,
                          dbname_from, dbname_to, model):
    """ Method to define which record needs to be update or not before importing it
    """
    sock_from, uid_from = get_socket(username_from, pwd_from, dbname_from, 8069)
    sock_to, uid_to = get_socket(username_to, pwd_to, dbname_to, 8169)
    mapping_xml_id = {}
    mapping_list = []
    for m in model:

        records_source = sock_from.execute(dbname_from, uid_from, pwd_from,
                                           'ir.model.data', 'search',
                                           [('model', '=', m)])
        if records_source:
            for r in records_source:
                xml_id_source = get_xml_id_source(r, username_from, pwd_from,
                                                  dbname_from, m)

                xml_id_destination = get_xml_id_destination(xml_id_source,
                                                            username_to, pwd_to,
                                                            dbname_to, m)
                if xml_id_destination:
                    if (xml_id_source['name'] == xml_id_destination['name']
                            and xml_id_source['id'] != xml_id_destination['id']):
                        data = {
                            'xml_id': xml_id_source['name'],
                            'res_id_source': xml_id_source['id'],
                            'res_id_destination': xml_id_destination['id'],
                        }
                        mapping_list.append(data)
                else:
                    print('XML_ID NOT FOUND')
        mapping_xml_id[m] = mapping_list


def get_destination_id(source_id, username_from, username_to, pwd_from, pwd_to,
                       dbname_from, dbname_to, model):

    sock_from, uid_from = get_socket(username_from, pwd_from, dbname_from, 8069)
    sock_to, uid_to = get_socket(username_to, pwd_to, dbname_to, 8169)

    id_model_data = get_xml_id_source(source_id, username_from, username_to,
                                      pwd_from, pwd_to, dbname_from,
                                      dbname_to, model)
    if id_model_data:

        destination_id = sock_to.execute(dbname_to, uid_to, pwd_to,
                                         'ir.model.data', 'read',
                                         id_model_data, ['res_id'])
        return destination_id
    return None


def get_xml_id_source(source_id, username_source, pwd_source,
                      dbname_from, model):

    sock_from, uid_from = get_socket(username_source, pwd_source, dbname_from, 8069)
    xml_id_source = sock_from.execute(dbname_from, uid_from, pwd_source,
                                      'ir.model.data', 'read',
                                      source_id, ['name'])
    if xml_id_source:
        return xml_id_source
    else:
        return None


def get_xml_id_destination(xml_id_source, username_to, pwd_to, dbname_to,
                           model):

    sock_to, uid_to = get_socket(username_to, pwd_to, dbname_to, 8169)
    xml_id_source = xml_id_source['name']
    id_model_data = sock_to.execute(dbname_to, uid_to, pwd_to,
                                    'ir.model.data', 'search',
                                    [('name', '=', xml_id_source)])
    if id_model_data:
        xml_id_data = sock_to.execute(dbname_to, uid_to, pwd_to,
                                      'ir.model.data',
                                      'read', id_model_data,
                                      ['name'])
        return xml_id_data[0]
    return None


def get_socket(username, pwd, dbname, port):

    str_common = 'http://localhost:%s/xmlrpc/common' % port
    str_object = 'http://localhost:%s/xmlrpc/object' % port
    sock_common = xmlrpclib.ServerProxy(str_common)
    uid = sock_common.login(dbname, username, pwd)
    sock = xmlrpclib.ServerProxy(str_object)
    return sock, uid


#get_dependencies(username, pwd, dbname, models, excluded_models)
