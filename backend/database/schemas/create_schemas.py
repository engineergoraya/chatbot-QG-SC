from database.connection.database_connection import connection, cursor

def execute_queries(queries_list, department_name, view_or_schema):
    print("Creating " + department_name + " " + view_or_schema + " ....")

    for query in queries_list:
        cursor.execute(query)
        print("Schema created")
        connection.commit()

    print(department_name + " " + view_or_schema + " created successfully....")