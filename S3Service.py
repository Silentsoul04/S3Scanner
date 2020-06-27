"""
    This will be a service that the client program will instantiate to then call methods
    passing buckets
"""
import boto3 # TODO: Limit import to just boto3.client, probably
from s3Bucket import s3Bucket, BucketExists, Permission, s3BucketObject
from botocore.exceptions import ClientError
import botocore.session
from botocore import UNSIGNED
from botocore.client import Config
import datetime
from exceptions import AccessDeniedException

allUsersURI = 'uri=http://acs.amazonaws.com/groups/global/AllUsers'
authUsersURI = 'uri=http://acs.amazonaws.com/groups/global/AuthenticatedUsers'


class S3Service:
    def __init__(self, forceNoCreds=False):
        """Service constructor

        Arguments:
            forceNoCreds {boolean} - Setting to true forces the client to make requests as if we don't have AWS credentials
        """
        # Check for AWS credentials
        session = botocore.session.get_session()
        if forceNoCreds or session.get_credentials() is None or session.get_credentials().access_key is None:
            self.aws_creds_configured = False
            self.s3_client = boto3.client('s3', config=Config(signature_version=UNSIGNED))
        else:
            self.aws_creds_configured = True
            self.s3_client = boto3.client('s3')

        del session  # No longer needed

    def check_bucket_exists(self, bucket):
        if not isinstance(bucket, s3Bucket):
            raise ValueError("Passed object was not type s3Bucket")

        bucket_exists = True

        try:
            self.s3_client.head_bucket(Bucket=bucket.name)
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                bucket_exists = False

        bucket.exists = BucketExists.YES if bucket_exists else BucketExists.NO

    def check_perm_read_acl(self, bucket):
        """
            Check for the READACP permission on the bucket by trying to get the bucket ACL.

            Exceptions:
                ValueError
                ClientError
        """
        if bucket.exists != BucketExists.YES:
            raise ValueError("Bucket might not exist")  # TODO: Create custom exception for easier handling

        try:
            bucket.foundACL = self.s3_client.get_bucket_acl(Bucket=bucket.name)
        except ClientError as e:
            if e.response['Error']['Code'] == "AccessDenied" or e.response['Error']['Code'] == "AllAccessDisabled":
                if self.aws_creds_configured:
                    bucket.AuthUsersReadACP = Permission.DENIED
                else:
                    bucket.AllUsersReadACP = Permission.DENIED
            else:
                raise e

        self.parse_found_acl(bucket)  # If we can read ACLs, we know the rest of the permissions

    def check_perm_read(self, bucket):
        """
            Checks for the READ permission on the bucket by attempting to list the objects.

            Exceptions:
                ValueError
                ClientError
        """
        if bucket.exists != BucketExists.YES:
            raise ValueError("Bucket might not exist")  # TODO: Create custom exception for easier handling

        list_bucket_perm_allowed = True
        try:
            self.s3_client.list_objects_v2(Bucket=bucket.name, MaxKeys=0)  # TODO: Compare this to doing a HeadBucket
        except ClientError as e:
            if e.response['Error']['Code'] == "AccessDenied" or e.response['Error']['Code'] == "AllAccessDisabled":
                list_bucket_perm_allowed = False
            else:
                print("ERROR: Error while checking bucket {b}".format(b=bucket.name))
                raise e
        if self.aws_creds_configured:
            # Don't mark AuthUsersRead as Allowed if it's only implicitly allowed due to AllUsersRead being allowed
            # We only want to make AuthUsersRead as Allowed if that permission is explicitly set for AuthUsers
            if bucket.AllUsersRead != Permission.ALLOWED:
                bucket.AuthUsersRead = Permission.ALLOWED if list_bucket_perm_allowed else Permission.DENIED
        else:
            bucket.AllUsersRead = Permission.ALLOWED if list_bucket_perm_allowed else Permission.DENIED

    def check_perm_write(self, bucket):
        """ Check for WRITE permission by trying to upload an empty file to the bucket.

            File is named the current timestamp to ensure we're not overwriting an existing file in the bucket.
        """
        if bucket.exists != BucketExists.YES:
            raise ValueError("Bucket might not exist")  # TODO: Create custom exception for easier handling

        timestamp_file = str(datetime.datetime.now().timestamp()) + '.txt'

        try:
            # Try to create a new empty file with a key of the timestamp
            self.s3_client.put_object(Bucket=bucket.name, Key=timestamp_file, Body=b'')

            if self.aws_creds_configured:
                # Only set AuthUsersWrite to Allowed if it's explicitly set for AuthUsers
                # Don't set AuthUsersWrite to Allowed if it's implicitly allowed through AllUsers being Allowed
                if bucket.AllUsersWrite != Permission.ALLOWED:
                    bucket.AuthUsersWrite = Permission.ALLOWED
            else:
                bucket.AllUsersWrite = Permission.ALLOWED

            # Delete the temporary file
            self.s3_client.delete_object(Bucket=bucket.name, Key=timestamp_file)
        except ClientError as e:
            if e.response['Error']['Code'] == "AccessDenied" or e.response['Error']['Code'] == "AllAccessDisabled":
                if self.aws_creds_configured:
                    bucket.AuthUsersWrite = Permission.DENIED
                else:
                    bucket.AllUsersWrite = Permission.DENIED
            else:
                raise e
        finally:
            pass

    def check_perm_write_acl(self, bucket):
        """
        Checks for WRITE_ACP permission by attempting to set an ACL on the bucket. WARNING: Potentially destructive
        Make sure to run this check last as it will include all discovered permissions in the ACL it tries to set,
            thus ensuring minimal disruption for the bucket owner.
        """
        if bucket.exists != BucketExists.YES:
            raise ValueError("Bucket might not exist")  # TODO: Create custom exception for easier handling

        # TODO: See if there's a way to simplify this section
        readURIs = []
        writeURIs = []
        readAcpURIs = []
        writeAcpURIs = []
        fullControlURIs = []

        if bucket.AuthUsersRead == Permission.ALLOWED:
            readURIs.append(authUsersURI)
        if bucket.AuthUsersWrite == Permission.ALLOWED:
            writeURIs.append(authUsersURI)
        if bucket.AuthUsersReadACP == Permission.ALLOWED:
            readAcpURIs.append(authUsersURI)
        if bucket.AuthUsersWriteACP == Permission.ALLOWED:
            writeAcpURIs.append(authUsersURI)
        if bucket.AuthUsersFullControl == Permission.ALLOWED:
            fullControlURIs.append(authUsersURI)

        if bucket.AllUsersRead == Permission.ALLOWED:
            readURIs.append(allUsersURI)
        if bucket.AllUsersWrite == Permission.ALLOWED:
            writeURIs.append(allUsersURI)
        if bucket.AllUsersReadACP == Permission.ALLOWED:
            readAcpURIs.append(allUsersURI)
        if bucket.AllUsersWriteACP == Permission.ALLOWED:
            writeAcpURIs.append(allUsersURI)
        if bucket.AllUsersFullControl == Permission.ALLOWED:
            fullControlURIs.append(allUsersURI)

        if self.aws_creds_configured:   # Otherwise AWS will return "Request was missing a required header"
            writeAcpURIs.append(authUsersURI)
        else:
            writeAcpURIs.append(allUsersURI)
        args = {'Bucket': bucket.name}
        if len(readURIs) > 0:
            args['GrantRead'] = ','.join(readURIs)
        if len(writeURIs) > 0:
            args['GrantWrite'] = ','.join(writeURIs)
        if len(readAcpURIs) > 0:
            args['GrantReadACP'] = ','.join(readAcpURIs)
        if len(writeAcpURIs) > 0:
            args['GrantWriteACP'] = ','.join(writeAcpURIs)
        if len(fullControlURIs) > 0:
            args['GrantFullControl'] = ','.join(fullControlURIs)
        try:
            self.s3_client.put_bucket_acl(**args)
            if self.aws_creds_configured:
                # Don't mark AuthUsersWriteACP as Allowed if it's due to implicit permission via AllUsersWriteACP
                # Only mark it as allowed if the AuthUsers group is explicitly allowed
                if bucket.AllUsersWriteACP != Permission.ALLOWED:
                    bucket.AuthUsersWriteACP = Permission.ALLOWED
            else:
                bucket.AllUsersWriteACP = Permission.ALLOWED
        except ClientError as e:
            if e.response['Error']['Code'] == "AccessDenied" or e.response['Error']['Code'] == "AllAccessDisabled":
                if self.aws_creds_configured:
                    bucket.AuthUsersWriteACP = Permission.DENIED
                else:
                    bucket.AllUsersWriteACP = Permission.DENIED
            else:
                raise e

    def enumerate_bucket_objects(self, bucket):
        """
        Raises: AccessDeniedException - if bucket doesn't have READ permission
        """
        if bucket.exists == BucketExists.UNKNOWN:
            self.check_bucket_exists(bucket)
        if bucket.exists == BucketExists.NO:
            raise Exception("Bucket doesn't exist")

        try:
            for page in self.s3_client.get_paginator("list_objects_v2").paginate(Bucket=bucket.name):
                if 'Contents' not in page:  # No items in this bucket
                    bucket.objects_enumerated = True
                    return
                for item in page['Contents']:
                    obj = s3BucketObject(key=item['Key'], last_modified=item['LastModified'], size=item['Size'])
                    bucket.addObject(obj)
        except ClientError as e:
            if e.response['Error']['Code'] == "AccessDenied" or e.response['Error']['Code'] == "AllAccessDisabled":
                raise AccessDeniedException("AccessDenied while enumerating bucket objects")
        bucket.objects_enumerated = True

    def parse_found_acl(self, bucket):
        """
        If we were able to read the ACLs, we should be able to skip manually checking most permissions

        :param bucket:
        :return:
        """

        if bucket.foundACL is None:
            return

        if 'Grants' in bucket.foundACL:
            for grant in bucket.foundACL['Grants']:
                if grant['Grantee']['Type'] == 'Group':
                    if 'URI' in grant['Grantee'] and grant['Grantee']['URI'] == 'http://acs.amazonaws.com/groups/global/AuthenticatedUsers':
                        # Permissions have been given to the AuthUsers group
                        if grant['Permission'] == 'FULL_CONTROL':
                            bucket.AuthUsersRead = Permission.ALLOWED
                            bucket.AuthUsersWrite = Permission.ALLOWED
                            bucket.AuthUsersReadACP = Permission.ALLOWED
                            bucket.AuthUsersWriteACP = Permission.ALLOWED
                            bucket.AuthUsersFullControl = Permission.ALLOWED
                        elif grant['Permission'] == 'READ':
                            bucket.AuthUsersRead = Permission.ALLOWED
                        elif grant['Permission'] == 'READ_ACP':
                            bucket.AuthUsersReadACP = Permission.ALLOWED
                        elif grant['Permission'] == 'WRITE':
                            bucket.AuthUsersWrite = Permission.ALLOWED
                        elif grant['Permission'] == 'WRITE_ACP':
                            bucket.AuthUsersWriteACP = Permission.ALLOWED

                    elif 'URI' in grant['Grantee'] and grant['Grantee']['URI'] == 'http://acs.amazonaws.com/groups/global/AllUsers':
                        # Permissions have been given to the AllUsers group
                        if grant['Permission'] == 'FULL_CONTROL':
                            bucket.AllUsersRead = Permission.ALLOWED
                            bucket.AllUsersWrite = Permission.ALLOWED
                            bucket.AllUsersReadACP = Permission.ALLOWED
                            bucket.AllUsersWriteACP = Permission.ALLOWED
                            bucket.AllUsersFullControl = Permission.ALLOWED
                        elif grant['Permission'] == 'READ':
                            bucket.AllUsersRead = Permission.ALLOWED
                        elif grant['Permission'] == 'READ_ACP':
                            bucket.AllUsersReadACP = Permission.ALLOWED
                        elif grant['Permission'] == 'WRITE':
                            bucket.AllUsersWrite = Permission.ALLOWED
                        elif grant['Permission'] == 'WRITE_ACP':
                            bucket.AllUsersWriteACP = Permission.ALLOWED

            # All permissions not explicitly granted in the ACL are denied
            # TODO: Simplify this
            if bucket.AuthUsersRead == Permission.UNKNOWN:
                bucket.AuthUsersRead = Permission.DENIED

            if bucket.AuthUsersWrite == Permission.UNKNOWN:
                bucket.AuthUsersWrite = Permission.DENIED

            if bucket.AuthUsersReadACP == Permission.UNKNOWN:
                bucket.AuthUsersReadACP = Permission.DENIED

            if bucket.AuthUsersWriteACP == Permission.UNKNOWN:
                bucket.AuthUsersWriteACP = Permission.DENIED

            if bucket.AuthUsersFullControl == Permission.UNKNOWN:
                bucket.AuthUsersFullControl = Permission.DENIED

            if bucket.AllUsersRead == Permission.UNKNOWN:
                bucket.AllUsersRead = Permission.DENIED

            if bucket.AllUsersWrite == Permission.UNKNOWN:
                bucket.AllUsersWrite = Permission.DENIED

            if bucket.AllUsersReadACP == Permission.UNKNOWN:
                bucket.AllUsersReadACP = Permission.DENIED

            if bucket.AllUsersWriteACP == Permission.UNKNOWN:
                bucket.AllUsersWriteACP = Permission.DENIED

            if bucket.AllUsersFullControl == Permission.UNKNOWN:
                bucket.AllUsersFullControl = Permission.DENIED