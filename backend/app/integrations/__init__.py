"""External provider adapters (SPEC §6.14).

The only place ``sendgrid``, ``twilio``, or ``boto3`` may be imported. Keeps the
application portable and gives tests a single seam to stub.
"""
