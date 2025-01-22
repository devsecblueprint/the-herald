import os
import logging
import re
from datetime import datetime, timedelta

import boto3
from serpapi import GoogleSearch

TABLE_ARN = os.environ["DYNAMODB_TABLE_ARN"]
API_TOKEN_PARAMETER = os.environ["SERPAPI_TOKEN_PARAMETER"]
PREFERRED_JOB_BOARDS = ["LinkedIn"]
QUERIES = ["devsecops", "cloud security"]

# Logging Configuration
logging.getLogger().setLevel(logging.INFO)

# Parameters for Google Job Search API
PARAMS = {
    "api_key": "",
    "engine": "google_jobs",
    "google_domain": "google.com",
    "q": "devsecops",
    "ltype": "1",
}


def main(event, _):
    """
    This is the main entry point for the Lambda function.
    It takes in the event and context as arguments, and returns the response.
    """
    logging.info("Event: %s", event)

    PARAMS["api_key"] = get_serpapi_token()
    available_jobs = list_available_jobs()
    publish_jobs_to_table(available_jobs)

    return {"All of the jobs have been processed.": available_jobs}


def list_available_jobs():
    """
    Lists available jobs from the SerpAPI, which pulls that information
    from the Google Job Search APIs.
    """
    available_jobs = []

    for query in QUERIES:
        PARAMS["q"] = query
        logging.info("Querying for jobs related to %s", query)

        search = GoogleSearch(PARAMS)
        results = search.get_dict()

        for result in results["jobs_results"]:
            title = result["title"]
            share_link = result["share_link"]
            company_name = result["company_name"]
            is_remote = False

            for apply_options in result["apply_options"]:
                if apply_options["title"] in PREFERRED_JOB_BOARDS:
                    logging.info(
                        "Title: %s has a preferred job board: %s",
                        title,
                        apply_options["title"],
                    )
                    share_link = apply_options["link"]
                    break

            posted_at = result["detected_extensions"].get("posted_at", None)

            if posted_at is None:
                logging.info(
                    "Title: %s does not have a posted_at field. We'll skip this.", title
                )
                continue
            if not is_less_than_7_days_old(posted_at):
                logging.info(
                    "Title: %s is not posted exactly 7 days ago. We'll skip this.",
                    title,
                )
                continue
            if result["detected_extensions"].get("work_from_home"):
                logging.info("Title: %s is a remote position.", title)
                is_remote = True

            job_information = {
                "title": title,
                "company_name": company_name,
                "share_link": share_link,
                "is_remote": is_remote,
            }
            available_jobs.append(job_information)

    return available_jobs


def publish_jobs_to_table(available_jobs: list):
    """
    Inserts jobs into a DynamoDB table.
    """
    logging.info("Inserting jobs into DynamoDB")
    logging.info("Available Jobs Count: %s", len(available_jobs))

    dynamodb_client = boto3.client("dynamodb")

    for job in available_jobs:
        logging.info("Available Job: %s", job)

        response = dynamodb_client.put_item(
            TableName=TABLE_ARN,
            Item={
                "type": {"S": "job"},
                "link": {"S": job["share_link"]},
                "title": {"S": job["title"]},
                "companyName": {"S": job["company_name"]},
                "isRemote": {"BOOL": job["is_remote"]},
                "expirationDate": {
                    "S": str(int((datetime.now() + timedelta(days=1)).timestamp()))
                },
            },
        )
        logging.info("Response: %s", response)


def is_less_than_7_days_old(posted_string):
    """
    Determines if a job was posted exactly 7 days ago.

    Args:
        posted_string (str): A string like '13 days ago' or '7 days ago'.

    Returns:
        bool: True if the job was posted exactly 7 days ago, False otherwise.
    """
    # Extract the number of days from the string using regex
    match = re.search(r"(\d+)\s+(days?|hours?)\s+ago", posted_string)
    if match:
        value = int(match.group(1))  # Extract the number
        unit = match.group(2)  # Extract the time unit (day/hour)

        # Check if it's 7 days or less
        if unit.startswith("day"):
            return value <= 7
        if unit.startswith("hour"):
            return True  # If it's in hours, it's within 7 days

    return False  # Return False if the string does not match the pattern


def get_serpapi_token():
    """
    Retrieves the SerpAPI token from SSM.
    """
    client = boto3.client("ssm")
    response = client.get_parameter(Name=API_TOKEN_PARAMETER, WithDecryption=True)
    return response["Parameter"]["Value"]
