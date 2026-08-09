#
# Copyright (c) 2024 Nordic Semiconductor ASA
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
#

cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(gps_mqtt_tracker)

target_sources(app PRIVATE
  src/main.c
  src/tracker_gnss.c
  src/tracker_modem.c
  src/tracker_mqtt.c
  src/tracker_ui.c
)

if(CONFIG_TRACKER_CIRCUIT_DOJO_GNSS_SETUP)
  target_sources(app PRIVATE src/circuitdojo_gnss.c)
endif()
