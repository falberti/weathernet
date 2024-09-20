# create schema
CREATE SCHEMA sensors;

# create the user for a probe
CREATE ROLE user WITH PASSWORD 'password';
ALTER ROLE user WITH LOGIN;
GRANT CONNECT ON DATABASE weather TO user;
GRANT INSERT ON ALL TABLES IN SCHEMA sensors TO user;
