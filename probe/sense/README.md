# Probes

## Virtual environments

Create the virtual env:
'''
$ python3 -m venv venv/
'''

Activate the virtual env:
'''
$ source venv/bin/activate
(venv) $
'''

## SQLite3

Install SQLIte3:
'''
$ sudo apt install sqlite3
'''

Every sensor will have its own local DB. Create it with:
'''
$ sqlite3 local.sqlite3
'''

We will now create tables for each sensor.
We will store datatime of the read as the INTEGER representing its Unix Time, the number of seconds since 1970-01-01 00:00:00 UTC.

### BMP280
'''
CREATE TABLE bmp280(datetime INTEGER PRIMARY KEY, temperature NUMERIC(3,2), pressure NUMERIC(5,2));
'''
