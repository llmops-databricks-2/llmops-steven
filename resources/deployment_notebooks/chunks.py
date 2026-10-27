# Databricks notebook source
"""Rebuild the chunks table from the articles table, at article and paragraph level, with embeddings."""

from databricks.sdk.runtime import dbutils

from vehicle_rules.chunks import main

# The environment comes from the job's base parameters; "dev" when run by hand.
dbutils.widgets.text("env", "dev")
main(dbutils.widgets.get("env"))
