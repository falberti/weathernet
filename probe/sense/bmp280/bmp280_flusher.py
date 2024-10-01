#!/usr/bin/env python3

import psycopg2
import os
import sqlite3
import random
import time

SLEEP_MIN = 20
SLEEP_MAX = 120

timescale_conn = psycopg2.connect(
                       host=os.environ['DB_HOST'],
                       port=os.environ['DB_PORT'],
                       dbname=os.environ['DB_NAME'],
                       user=os.environ['DB_USER'],
                       password=os.environ['DB_PASSWORD'],
                       sslmode='verify-ca',
                       sslrootcert='/opt/mtls/cacert.pem',
                       sslcert='/opt/mtls/client.cert.pem',
                       sslkey='/opt/mtls/client.key.pem'
                       )
timescale_cursor = timescale_conn.cursor()

while True:
    try:
        sqlite3_conn = sqlite3.connect('local.sqlite3')
        sqlite3_conn.row_factory = sqlite3.Row  
        sqlite3_cur = sqlite3_conn.cursor()
        sqlite3_query = "SELECT * from bmp280"
        sqlite3_cur.execute(sqlite3_query)
        rows = sqlite3_cur.fetchall()
        
        timescale_query = "INSERT INTO bmp280(probed, temperature, pressure) VALUES (to_timestamp(%s), %s, %s);" 
        for row in rows:
            print(dict(row))
            timescale_cursor.execute(timescale_query, (row['datetime'], row['temperature'], row['pressure']))
            sqlite3_new_cur = sqlite3_conn.cursor()
            sqlite3_new_cur.execute("DELETE FROM bmp280 WHERE datetime = ?", (row['datetime'],))
        
        timescale_conn.commit()
        sqlite3_conn.commit()
        sqlite3_conn.close()
    except sqlite3.OperationalError as e:
        print(e)
        pass
        # try again to connect
        time.sleep(1)

    time.sleep(random.randint(SLEEP_MIN, SLEEP_MAX))
