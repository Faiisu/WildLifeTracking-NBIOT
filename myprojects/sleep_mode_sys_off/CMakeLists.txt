#
# Copyright (c) 2026 Cellular Asset Tracker Project
# SPDX-License-Identifier: Apache-2.0
#

cmake_minimum_required(VERSION 3.20.0)

# Load local broker endpoint/credentials overlay when present (ignored by Git).
set(SLEEP_APP_MQTT_CREDENTIALS_FILE
  ${CMAKE_CURRENT_SOURCE_DIR}/config/mqtt-credentials.conf
)
if(EXISTS ${SLEEP_APP_MQTT_CREDENTIALS_FILE})
  set(EXTRA_CONF_FILE ${SLEEP_APP_MQTT_CREDENTIALS_FILE} ${EXTRA_CONF_FILE})
endif()

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(sleep_mode_sys_off)

# The broker CA is a local deployment asset (never commit to Git).
set(SLEEP_APP_CERT_GEN_DIR ${CMAKE_CURRENT_BINARY_DIR}/certs)
zephyr_include_directories(${SLEEP_APP_CERT_GEN_DIR})
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/credentials/ca-cert.pem)
  generate_inc_file_for_target(app credentials/ca-cert.pem
    ${SLEEP_APP_CERT_GEN_DIR}/ca-cert.pem)
endif()

target_sources(app PRIVATE
  src/main.c
  src/tracker_mqtt.c
)
