#
# Senior Project - GPS + NB-IoT
# Target board: sparkfun_thing_plus_nrf9160_ns
#
# Build:
#   west build -b sparkfun_thing_plus_nrf9160_ns
#
# Flash:
#   west flash
#
# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
#

cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(senior_project_gnss_nbiot)

# ---------------------------------------------------------------------------
# Core application source
# ---------------------------------------------------------------------------
zephyr_library_sources(src/main.c)

# ---------------------------------------------------------------------------
# GNSS assistance modules (selected via prj.conf Kconfig options)
# ---------------------------------------------------------------------------
zephyr_library_sources_ifdef(CONFIG_GNSS_SAMPLE_ASSISTANCE_NRF_CLOUD  src/assistance.c)
zephyr_library_sources_ifdef(CONFIG_GNSS_SAMPLE_ASSISTANCE_SUPL       src/assistance_supl.c)
zephyr_library_sources_ifdef(CONFIG_GNSS_SAMPLE_ASSISTANCE_MINIMAL    src/assistance_minimal.c)
zephyr_library_sources_ifdef(CONFIG_GNSS_SAMPLE_ASSISTANCE_MINIMAL    src/mcc_location_table.c)
