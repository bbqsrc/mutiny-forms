import sys
import simplebdd
import json

from simplebdd import Test, Description
from mock import Mock, MagicMock, patch
from tornado.web import StaticFileHandler, HTTPError

import os.path
join = simplebdd.import_path(os.path.split(__file__)[-1] + '/../forms/join.py')
paypal = simplebdd.import_path(os.path.split(__file__)[-1] + '/../forms/mutiny_paypal.py')


def mock_dict(lst):
    out = {}
    for i in lst:
        out[i] = "test"
    return out


class MockHandler:
    def __init__(self, *args, **kwargs):
        self.initialize()

    def render(self, *args, **kwargs):
        return

def get_paypal_mock():
    pp = paypal.PayPalAPI({})
    pp.paypal_request = MagicMock()
    pp.create_and_send_invoice = MagicMock()
    pp.get_merchant_info = MagicMock(return_value={
        'businessName': "test",
        "website": "www.test.nope"
    })
    return pp


class MockNewMemberFormHandler(MockHandler, join.NewMemberFormHandler):
    def initialize(self):
        self.name = "join"
        self.invoice_email = "test"
        self.config = {'email': "merchant@email.com"}
        self.paypal = get_paypal_mock()
        self.db = Mock()
        self.mutiny = Mock()
        self.mailer = Mock()

    def _get_counter(self, key):
        return 1

class JoinFormTests(Test):
    """Join form tests"""

    def before_tests(self):
        self.before_each_test()
        self.handler = MockNewMemberFormHandler()
        self.handler.send_admin_message = MagicMock()
        self.handler.get_argument = MagicMock(return_value=json.dumps(self.required))

    def before_each_test(self):
        self.required = mock_dict([
            'given_names', 'surname', 'date_of_birth', 'residential_address',
            'residential_postcode', 'residential_state', 'residential_suburb',
            'submission', 'declaration', 'email', 'primary_phone',
            'membership_level', 'payment_method'
        ])
        self.required['date_of_birth'] = "1/1/1980"
        self.required['submission'] = self.required['declaration'] = True
        self.required['membership_level'] = "full"
        self.required['payment_method'] = "paypal"
        self.required['email'] = "fake@email.com"

        self.optional = mock_dict([
            'gender', 'postal_address', 'postal_postcode', 'postal_state',
            'postal_suburb', 'secondary_phone', 'opt_out_state_parties',
            'other_party_in_last_12_months'
        ])

    class Validation(Description):
        """Validation stuff"""

        def before_each_test(self):
            self.required = self.test.required.copy()
            self.optional = self.test.optional.copy()

        def it_should_pass_if_all_required_fields(self):
            """It should pass if all required fields exist"""
            self.test.handler.validate(json.dumps(self.required))
            return True

        def it_should_fail_if_required_fields_missing(self):
            """It should fail if required fields missing"""
            try:
                self.test.handler.validate(json.dumps({
                    "surname": "Test"
                }))
            except HTTPError as e:
                if e.status_code == 400:
                    return True
            return False

        def it_should_allow_all_optional_fields(self):
            """It should allow all optional fields"""

            fields = self.required
            fields.update(self.optional)

            self.test.handler.validate(json.dumps(fields))
            return True

        def it_should_discard_undeclared_fields(self):
            """It should discard undeclared fields"""
            fields = self.required
            fields.update(self.optional)
            fields['foo'] = 42

            res = self.test.handler.validate(json.dumps(fields))
            return res.get('foo') is None

    class Record(Description):
        """Record-related stuff"""
        def before_each_test(self):
            self.required = self.test.required.copy()
            self.optional = self.test.optional.copy()

        def it_should_generate_valid_records(self):
            """It should generate valid records"""
            fields = self.required
            fields.update(self.optional)
            member_record = self.test.handler.create_member_record(fields)
            return True

    class Mailer(Description):
        """Mailer-related stuff"""

        def before_each_test(self):
            self.required = self.test.required.copy()
            self.optional = self.test.optional.copy()

        def it_should_send_custom_invoice_if_not_paypal(self):
            """It should send custom invoice if not PayPal"""
            fields = self.required
            fields.update(self.optional)
            fields['payment_method'] = "cheque"
            member_record = self.test.handler.create_member_record(fields)

            patcher = patch("join.Invoice")
            patcher.start()
            self.test.handler.create_and_send_invoice(
                    member_record['details'],
                    member_record['invoices'][0]
            )
            patcher.stop()
            return len(self.test.handler.paypal.create_and_send_invoice.mock_calls) == 0


        def it_should_use_paypal_api_if_selected(self):
            """It should use PayPal API if selected"""
            fields = self.required
            fields.update(self.optional)
            member_record = self.test.handler.create_member_record(fields)

            self.test.handler.create_and_send_invoice(
                    member_record['details'],
                    member_record['invoices'][0]
            )
            return len(self.test.handler.paypal.create_and_send_invoice.mock_calls) > 0


    class POST(Description):
        """POST related stuff"""

        def it_should_send_an_administration_email(self):
            """It should send an administration email when done its thing"""
            self.test.handler.post()
            return len(self.test.handler.send_admin_message.mock_calls) > 0

