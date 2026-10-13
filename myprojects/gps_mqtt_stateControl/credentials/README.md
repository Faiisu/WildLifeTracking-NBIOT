# MQTT TLS certificate

Place the broker root CA certificate in `ca-cert.pem` here. CMake embeds it in
firmware and the shared MQTT module provisions modem security tag 24 at startup.
This file is ignored by Git. Do not commit deployment credentials or private keys.
TLS initialization fails if the CA file is missing; a successful build alone does
not establish that the application can initialize or connect.
