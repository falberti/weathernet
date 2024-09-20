#!/usr/bin/env python3

import psycopg2

conn = psycopg2.connect(
        host='HOST',
        port='PORT',
        dbname='DBNAME',
        user='USER',
        password='PASSWORD',
        sslmode='verify-full',
        sslrootcert='cacert.pem',
        sslcert='client.cert.pem',
        sslkey='client.key.pem'
        )
cursor = conn.cursor()
# use the cursor to interact with your database
cursor.execute("SELECT 'hello world'")
print(cursor.fetchone())
