import datetime
import json
import re
import uuid
import logging
import yaml

import pymongo
import tornado.options
import tornado.web

from mutiny_paypal import PayPalAPI
from bson.json_util import dumps
from pymongo import Connection
from tornado.web import Application, HTTPError, RequestHandler, StaticFileHandler

class JoinFormHandler(tornado.web.RequestHandler):
    def initialize(self):
        self.name = "join"
        self.config = yaml.load(open('paypal.conf.yml'))['live']
        self.paypal = PayPalAPI(self.config)
        self.db = Connection().ppau
        self.mutiny = Connection().mutiny

    def validate(self, data):
        try:
            form_data = json.loads(data)
        except:
            raise HTTPError(400, "invalid form data")

        cleaned = {}
        fields = [
            'given_names', 'surname', 'date_of_birth', 'residential_address',
            'residential_postcode', 'residential_state', 'residential_suburb',
            'submission', 'declaration', 'email', 'primary_phone',
            'membership_level', 'payment_method'
        ]

        for field in fields:
            if field in form_data.keys():
                cleaned[field] = form_data[field]
            else:
                raise HTTPError(400, "missing fields: %s" % field)

        if cleaned['membership_level'] != "full":
            raise HTTPError(400, "invalid form data")

        if cleaned['payment_method'] not in ("paypal", "direct_deposit", "cheque"):
            raise HTTPError(400, "invalid form data")

        if cleaned['declaration'] != True or cleaned['submission'] != True:
            raise HTTPError(400, "invalid form data")

        try:
            cleaned['date_of_birth'] = datetime.datetime.strptime(cleaned['date_of_birth'], "%d/%m/%Y")
        except:
            raise HTTPError(400, "invalid form data")

        return cleaned

    def create_member_record(self, data):
        id = uuid.uuid4()
        ts = datetime.datetime.utcnow()

        details = {
            "given_names": data['given_names'],
            "surname": data['surname'],
            "date_of_birth": data['date_of_birth'],
            "gender": data.get('gender', None),
            "residential_address": data['residential_address'],
            "residential_suburb": data['residential_suburb'],
            "residential_state": data['residential_state'],
            "residential_postcode": data['residential_postcode'],
            "postal_address": data.get('postal_address', None),
            "postal_suburb": data.get('postal_suburb', None),
            "postal_state": data.get('postal_state', None),
            "postal_postcode": data.get('postal_postcode', None),
            "email": data['email'],
            "primary_phone": data['primary_phone'],
            "secondary_phone": data.get('secondary_phone', None),
            "membership_level": data['membership_level'],
            "opt_out_state_parties": data.get('opt_out_state_parties', False),
            "other_party_in_last_12_months": data.get('other_party_in_last_12_months', None),
            "joined_on": ts
        }

        invoice = self.create_invoice_record(data['membership_level'], data['payment_method'])

        return {
            "_id": id,
            "history": [{
                "action": "new",
                "ts": ts,
                "details": details,
                "v": 1
            },
            {
                "action": "new-invoice",
                "ts": invoice['ts'],
                "invoice": invoice,
                "v": 1
            }],
            "invoices": [invoice],
            "details": details,
            "v": 1
        }

    def create_invoice_record(self, membership_level, payment_method):
        if membership_level == "full":
            price = 2000

        return {
            "v": 1,
            "ts": datetime.datetime.utcnow(),
            "items": [{
                "item": "Full Membership - 12 Months",
                "qty": 1,
                "price": price
            }],
            "payment_method": payment_method,
            "reference": "FM%s" % self._get_counter('new_member')
        }

    def create_and_send_invoice(self, member, invoice):
        if invoice['payment_method'] == "paypal":
            biller_info = self.paypal.create_biller_info(
                member['given_names'],
                member['surname'],
                member['primary_phone'],
                member['residential_address'],
                None,
                member['residential_suburb'],
                member['residential_state'],
                member['residential_postcode']
            )

            logging.debug(self.paypal.create_and_send_invoice(
                self.config['email'],
                member['email'],
                self.paypal.get_merchant_info(),
                biller_info,
                self.paypal.create_invoice_item(invoice['items'][0]['item'], str(invoice['items'][0]['price']/100))
                payment_terms="Net30"
            ))
        elif invoice['payment_method'] == "direct_deposit":
            pass
        elif invoice['payment_method'] == "cheque":
            pass
        else:
            raise Exception("How did you even get here?")

    def get(self):
        self.render(self.name + '.html')

    def post(self):
        data = self.validate(self.get_argument('data', None))
        member_record = self.create_member_record(data)
        self.create_and_send_invoice(member_record['details'], member_record['invoices'][0])
        self.write("<pre>" + dumps(member_record, indent=2) + "</pre>")

    def _get_counter(self, name):
        record = self.db.counters.find_one({"_id": name})
        if record is None:
            self.db.counters.insert({"_id": name, "count": 1})
            return 1
        else:
            self.db.counters.update({"_id": name}, {"$inc": {"count": 1}})
            return record['count'] + 1


if __name__ == "__main__":
    tornado.options.parse_command_line()
    application = Application([
        (r"/", JoinFormHandler),
        (r"/static/(.*)", StaticFileHandler, {"path": "../static"})
    ], **{"template_path": "../templates"})
    application.listen(8888)
    tornado.ioloop.IOLoop.instance().start()

