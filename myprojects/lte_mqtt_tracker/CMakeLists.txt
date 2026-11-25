#
# Copyright (c) 2024 Nordic Semiconductor ASA
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
#

cmake_minimum_required(VERSION 3.20.0)

# Keep default application configuration grouped by subsystem. prj.conf remains
# the standard Zephyr entry point, while these fragments are merged in order
# before any command-line overlay configuration.
set(TRACKER_CONF_FILES
  ${CMAKE_CURRENT_SOURCE_DIR}/config/system.conf
  ${CMAKE_CURRENT_SOURCE_DIR}/config/logging.conf
  ${CMAKE_CURRENT_SOURCE_DIR}/config/hardware.conf
  ${CMAKE_CURRENT_SOURCE_DIR}/config/networking.conf
  ${CMAKE_CURRENT_SOURCE_DIR}/config/modem.conf
  ${CMAKE_CURRENT_SOURCE_DIR}/config/mqtt.conf
  ${CMAKE_CURRENT_SOURCE_DIR}/config/tracker.conf
)

# Load local broker endpoint/credentials overlay when present (ignored by Git).
set(TRACKER_MQTT_CREDENTIALS_FILE
  ${CMAKE_CURRENT_SOURCE_DIR}/config/mqtt-credentials.conf
)
if(EXISTS ${TRACKER_MQTT_CREDENTIALS_FILE})
  list(APPEND TRACKER_CONF_FILES ${TRACKER_MQTT_CREDENTIALS_FILE})
endif()

set(EXTRA_CONF_FILE ${TRACKER_CONF_FILES} ${EXTRA_CONF_FILE})

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(lte_mqtt_tracker)

# The broker CA is a local deployment asset: provision it into the modem at
# startup, but never add it to Git. See credentials/README.md.
set(TRACKER_CERT_GEN_DIR ${CMAKE_CURRENT_BINARY_DIR}/certs)
zephyr_include_directories(${TRACKER_CERT_GEN_DIR})
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/credentials/ca-cert.pem)
  generate_inc_file_for_target(app credentials/ca-cert.pem
    ${TRACKER_CERT_GEN_DIR}/ca-cert.pem)
endif()

target_sources(app PRIVATE
  src/main.c
  src/tracker_status.c
  src/tracker_backlog.c
  src/tracker_modem.c
  src/tracker_mqtt.c
  src/tracker_ui.c
)
