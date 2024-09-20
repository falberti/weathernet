# Mutual TLS

Mutual Transport Layer Security (mTLS) is an end-to-end security method for mutual authentication that ensures that both parties are who they claim to be before data is shared.

Mutual TLS is very similar to the TLS protocol. In mTLS, there’s an additional step involved before the key exchange. The client sends its public key and certificate to the server, which the server verifies to identify the request is coming from a known client and has the private key corresponding to the public key that the client shared.

## Set-up of the environment

### Creating the CA Certificate
First, we need a Certificate Authority (CA) certificate that can sign both client and server certificates.

    # Create the necessary directory structure  
    mkdir -p /root/mtls/{certs,private}  
	
    # Create index.txt and serial files to keep track of signed certificates  
    cd /root/mtls/  
    echo 01 > serial  
    touch index.txt  
     
    # Copy the openssl.cnf file from /etc/pki/tls/ and make modifications to it  
    cp /etc/pki/tls/openssl.cnf .  
  
    # ***TODO - Within openssl.cnf, update the following attributes ***  
    dir = /root/mtls
    new_certs_dir = $dir/certs  
    certificate = $dir/certs/cacert.pem
    countryName_default = IT
    stateOrProvinceName_default = Italy
    localityName_default = optional
    0.organizationName_default = Falberti
    organizationalUnitName_default = Falberti
  
    # Generate the private key for the CA certificate  
    openssl genrsa -out private/cakey.pem 4096  
  
    # Create the CA certificate  
    openssl req -new -x509 -days 3650 -config /root/mtls/openssl.cnf -key private/cakey.pem -out certs/cacert.pem

### Converting the Certificate to PEM Format

To convert the certificate to PEM format, use the following command:

    openssl x509 -in certs/cacert.pem -out certs/cacert.pem -outform PEM
    
### Creating a Client Certificate

For the client, follow these steps:

    # Create a directory for client certificates.  
    mkdir /root/client_certs  
    cd /root/client_certs/  
  
    # Generate the private key for the client.  
    openssl genrsa -out client.key.pem 4096  
  
    # Generating a Certificate Signing Request (CSR) for the Client  
    # Remember to revise the Common Name (CN) to match the client's hostname, e.g., "client.yourdomain.com."  
    openssl req -new -key client.key.pem -out client.csr   
  
    # Creating the Client Certificate  
    openssl ca -config /root/mtls/openssl.cnf -days 1650 -notext -batch -in client.csr -out client.cert.pem  
  
    # The certificate information in the database will be updated with this command.  
    # You can check the serial number with  
    cat /root/mtls/index.txt  
    openssl x509 -in client.cert.pem -noout -serial

### Creating a Server Certificate

For the server, follow similar steps:

```
# Create a directory for server certificates  
mkdir /root/server_certs  
cd /root/server_certs/  
  
# Generate the private key for the server  
openssl genrsa -out server.key.pem 4096  
  
# Generating a Certificate Signing Request (CSR) for the Server.  
# Remember to revise the Common Name (CN) to match the server's hostname, e.g., "server.yourdomain.com."  
openssl req -new -key server.key.pem -out server.csr  
   
# Creating the Server Certificate  
openssl ca -config /root/mtls/openssl.cnf -days 1650 -notext -batch -in server.csr -out server.cert.pem  
  
# The certificate information in the database will be updated with this command.   
# You can check the serial number with  
cat /root/mtls/index.txt  
openssl x509 -in server.cert.pem -noout -serial
```
### Validating Mutual TLS Authentication in Linux

To ensure the mutual TLS authentication is working, execute the following commands:

On the server node:

    openssl s_server -accept 3000 -CAfile /root/mtls/certs/cacert.pem -cert /root/server_certs/server.cert.pem -key /root/server_certs/server.key.pem -state

On the client node:

    openssl s_client -connect 127.0.0.1:3000 -key /root/client_certs/client.key.pem -cert /root/client_certs/client.cert.pem -CAfile /root/mtls/certs/cacert.pem -state

If all the configurations are accurate, you will witness a successful TCP handshake between the server and client nodes, indicating that mutual TLS authentication is operational.

### Distribute the keys to the client

At this point you can copy the three files to the client.
```
/root/client_certs/client.key.pem
/root/client_certs/client.cert.pem
/root/mtls/certs/cacert.pem
```

## Secure PostgreSQL

### Configure  PostgreSQL  to authenticate itself with its TLS certificate

We now want to instruct our  PostgreSQL  server to identify itself using the certificate issued in the last step and to force clients to connect over TLS.

To start PostgreSQL in SSL mode, first enable SSL in  `postgresql.conf`.

```
# ...
ssl  =  on
# ...
```

Put your  `server.crt`  and  `server.key`  files in your installation's data directory, often at  `/var/lib/pgsql/data`  or  `/usr/local/pgsql/data`. Make sure their filenames are  `server.crt`  and  `server.key`  respectively, which are the expected defaults.

```
$ sudo cp server.crt /var/lib/pgsql/data/server.crt
$ sudo cp server.key /var/lib/pgsql/data/server.key
```

You'll need to ensure that PostgreSQL has access to the files and set the private key file permissions to disallow access to world or group.

```
$ sudo chown postgres:postgres /var/lib/pgsql/data/server.{crt,key}
$ sudo chmod 0600 /var/lib/pgsql/data/server.key
```

If you'd like to specify a different path for these files, manually configure them in  `postgresql.conf`.

```
# ...
ssl_cert_file = '/path/to/server.crt'
ssl_key_file = '/path/to/server.key'
# ...
```

In your  `pg_hba.conf`  file, change all records for non-local connections from  `host`  to  `hostssl`  to require clients to connect over TLS. It might look something like this.

```
# TYPE  DATABASE        USER            ADDRESS                 METHOD

# ...
hostssl all             all             all                     scram-sha-256
```

Finally, restart your PostgreSQL server for your changes to take effect.

### Configure  PostgreSQL  to require clients to authenticate with a certificate issued by your CA

To tell  PostgreSQL  to use mutual TLS and not just one-way TLS, we must instruct it to require client authentication to ensure clients present a certificate from our CA when they connect.

Move your  `ca.crt`  certificate to your PostgreSQL data directory—often at  `/var/lib/pgsql/data`  or  `/usr/local/pgsql/data`—and name it  `root.crt`  (the usual convention, though other paths are possible).

```
$ sudo cp ca.crt /var/lib/pgsql/data/root.crt
```

Make sure PostgreSQL has access to the file.

```
$ sudo chown postgres:postgres /var/lib/pgsql/data/root.crt
```

Configure  `postgresql.conf`  to point to your root CA certificate. PostgreSQL will use this certificate to verify certificates presented by clients.

```
# ...
ssl_ca_file = 'root.crt'
# ...
```

Configure  `pg_hba.conf`, creating  `hostssl`  records with the  `clientcert=1`  option for all relevant connections. It might look something like this:

```
# TYPE  DATABASE        USER            ADDRESS                 METHOD

# ...

# IPv4 remote connections for authenticated users
hostssl all             myuser          0.0.0.0/0               scram-sha-256 clientcert=verify-ca
```
Finally, restart your PostgreSQL server for your changes to take effect.

## Bibliography

https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-PARAMKEYWORDS

https://www.postgresql.org/docs/current/ssl-tcp.html#SSL-CLIENT-CERTIFICATES
