# Databricks notebook source
"""Set the comments on the RDW tables of the environment."""

from databricks.sdk.runtime import dbutils

from vehicle_rules.table_comments import main

# The environment comes from the job's base parameters; "dev" when run by hand.
dbutils.widgets.text("env", "dev")
main(dbutils.widgets.get("env"))
