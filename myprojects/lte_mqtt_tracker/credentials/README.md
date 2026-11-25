# Broker CA certificate

For a TLS broker, save its public CA chain in `ca-cert.pem`. The file is loaded
into `CONFIG_MQTT_HELPER_SEC_TAG` during tracker startup and is ignored by Git.
Do not store a private key here.
