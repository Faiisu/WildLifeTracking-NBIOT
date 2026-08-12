# Broker CA certificate

For a TLS broker, save its public CA chain in `ca-cert.pem`. The file is loaded
into the configured MQTT Helper security tag during tracker startup and is
ignored by Git. Do not store a private key here.

For the configured HiveMQ Cloud endpoint, use the public Let's Encrypt `ISRG
Root X1` certificate. It is intentionally a local deployment file, not a
versioned repository asset.
