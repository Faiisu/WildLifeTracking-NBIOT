# Broker CA certificate

For a TLS broker, save its public CA chain in `ca-cert.pem`. The file is loaded
into the configured MQTT Helper security tag during tracker startup and is
ignored by Git. Do not store a private key here.

If your broker is HiveMQ Cloud, use the public Let's Encrypt `ISRG Root X1`
certificate. For another TLS broker, use its own CA chain instead. This file
is intentionally a local deployment asset, not a versioned repository asset.
