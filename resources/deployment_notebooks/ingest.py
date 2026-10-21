# Databricks notebook source
"""Download the regulations in force into the project volume and rebuild the articles table."""

from databricks.sdk.runtime import dbutils

from vehicle_rules.ingest import main

# The environment comes from the job's base parameters; "dev" when run by hand.
dbutils.widgets.text("env", "dev")
main(dbutils.widgets.get("env"))
