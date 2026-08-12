#
# Copyright (c) 2024 Nordic Semiconductor ASA
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
#

cmake_minimum_required(VERSION 3.20.0)

set(MQTT_TEST_CONF_FILES
  ${CMAKE_CURRENT_SOURCE_DIR}/config/logging.conf
  ${CMAKE_CURRENT_SOURCE_DIR}/config/networking.conf
  ${CMAKE_CURRENT_SOURCE_DIR}/config/modem.conf
  ${CMAKE_CURRENT_SOURCE_DIR}/config/mqtt.conf
)

set(MQTT_TEST_CREDENTIALS_FILE
  ${CMAKE_CURRENT_SOURCE_DIR}/config/mqtt-credentials.conf
)
if(EXISTS ${MQTT_TEST_CREDENTIALS_FILE})
  list(APPEND MQTT_TEST_CONF_FILES ${MQTT_TEST_CREDENTIALS_FILE})
endif()

set(EXTRA_CONF_FILE ${MQTT_TEST_CONF_FILES} ${EXTRA_CONF_FILE})

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(mqtt_connection_test)

set(gen_dir ${CMAKE_CURRENT_BINARY_DIR}/certs)
zephyr_include_directories(${gen_dir})
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/credentials/ca-cert.pem)
  generate_inc_file_for_target(app credentials/ca-cert.pem ${gen_dir}/ca-cert.pem)
endif()

target_sources(app PRIVATE src/main.c)
