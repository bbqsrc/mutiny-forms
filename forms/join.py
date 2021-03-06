import datetime
import json
import re
import uuid
import logging
import yaml
import time

import pymongo
import tornado.options
import tornado.web

from invoice import Invoice
from bbqutils.email import sendmail, create_email, create_attachment
from mutiny_paypal import PayPalAPI
from bson.json_util import dumps
from pymongo import MongoClient
from tornado.web import Application, HTTPError, RequestHandler, StaticFileHandler
from tornado.options import define, options


define("no_mailer", default=False, help="Mock mailer")
define("mode", default="production", help="Mode (production/development)")
define("port", default=27013, help="port for server")


def safe_modify(col, query, update, upsert=False):
    for attempt in range(5):
        try:
            result = col.find_and_modify(
                    query=query,
                    update=update,
                    upsert=upsert,
                    new=True
            )
            return result
        except pymongo.errors.OperationFailure:
            return False
        except pymongo.errors.AutoReconnect:
            wait_t = 0.5 * pow(2, attempt)
            time.sleep(wait_t)
    return False


def safe_insert(collection, data):
    for attempt in range(5):
        try:
            collection.insert(data, safe=True)
            return True
        except pymongo.errors.OperationFailure:
            return False
        except pymongo.errors.AutoReconnect:
            wait_t = 0.5 * pow(2, attempt)
            time.sleep(wait_t)
    return False

db = MongoClient().ppau

class NewMemberFormHandler(tornado.web.RequestHandler):
    def initialize(self):
        self.name = "join"
        self.invoice_email = open('invoice-email.txt').read()
        self.welcome_email = open('welcome-email.txt').read()
        self.config = yaml.load(open('paypal.conf.yml'))[options.mode]
        self.paypal = PayPalAPI(self.config)

    def validate(self, data):
        try:
            form_data = json.loads(data)
        except:
            logging.error(data)
            raise HTTPError(400, "invalid form data")

        cleaned = {}
        mandatory_fields = [
            'given_names', 'surname', 'date_of_birth', 'residential_address',
            'residential_postcode', 'residential_state', 'residential_suburb',
            'submission', 'declaration', 'email', 'primary_phone',
            'membership_level', 'payment_method', 'payment_amount'
        ]

        optional_fields = [
            'gender', 'postal_address', 'postal_postcode', 'postal_state',
            'postal_suburb', 'secondary_phone', 'opt_out_state_parties',
            'other_party_in_last_12_months'
        ]

        for field in mandatory_fields:
            if field in form_data.keys():
                cleaned[field] = form_data[field]
            else:
                raise HTTPError(400, "missing fields: %s" % field)

        for field in optional_fields:
            if field in form_data.keys():
                cleaned[field] = form_data[field]

        if cleaned['membership_level'] not in ("full", "associate"):
            raise HTTPError(400, "invalid membership level")

        if cleaned['payment_method'] not in ("paypal", "direct_deposit", "cheque"):
            raise HTTPError(400, "invalid payment method")

        if cleaned['declaration'] != True or cleaned['submission'] != True:
            raise HTTPError(400, "invalid declaration or submission flag")

        try:
            x = abs(int(cleaned['payment_amount']) * 100)
            cleaned['payment_amount'] = x
            if x == 0:
                cleaned['payment_method'] = 'direct_deposit'
            #cleaned['payment_amount'] = 2000
        except:
            raise HTTPError(400, "invalid payment amount")

        try:
            cleaned['date_of_birth'] = datetime.datetime.strptime(cleaned['date_of_birth'], "%d/%m/%Y")
        except:
            logging.error(form_data)
            raise HTTPError(400, "invalid form data (date of birth)")

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

        invoice = self.create_invoice_record(data['membership_level'],
                                             data['payment_method'],
                                             data['payment_amount'])

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

    def create_invoice_record(self, membership_level, payment_method, price=None):
        if price is None:
            if membership_level in ("full", "associate"):
                price = 2000

        issued_date = datetime.datetime.utcnow()
        due_date = issued_date + datetime.timedelta(days=30)

        if membership_level == "full":
            out = {
                "v": 1,
                "ts": datetime.datetime.utcnow(),
                "items": [{
                    "item": "Full Membership - 12 Months",
                    "qty": 1,
                    "price": price
                }],
                "payment_method": payment_method,
                "due_date": due_date,
                "issued_date": issued_date,
                "status": "pending"
            }

            if payment_method != "paypal":
                c = self._get_counter('new_member')
                if c is None:
                    raise HTTPError(500, "mongodb keeled over at counter time")
                out["reference"] = "FM%s" % c

        elif membership_level == "associate":
            out = {
                "v": 1,
                "ts": datetime.datetime.utcnow(),
                "items": [{
                    "item": "Associate Membership - 12 Months",
                    "qty": 1,
                    "price": price
                }],
                "payment_method": payment_method,
                "due_date": due_date,
                "issued_date": issued_date,
                "status": "pending"
            }

            if payment_method != "paypal":
                c = self._get_counter('new_member_am')
                if c is None:
                    raise HTTPError(500, "mongodb keeled over at counter time")
                out["reference"] = "AM%s" % c
        return out

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

            response = self.paypal.create_and_send_invoice(
                self.config['email'],
                member['email'],
                self.paypal.get_merchant_info(),
                biller_info,
                self.paypal.create_invoice_item(invoice['items'][0]['item'], str(invoice['items'][0]['price']/100)),
                payment_terms="Net30"
            )

            if response['responseEnvelope']['ack'].startswith("Success"):
                invoice['reference'] = response['invoiceNumber']
                invoice['paypal_id'] = response['invoiceID']
                return invoice
            else:
                return None

        elif invoice['payment_method'] in ("direct_deposit", "cheque"):
            personal = {
              "name": "Pirate Party Australia Incorporated",
              "address": [],
              "contact": {
                "Email": "membership@pirateparty.org.au",
                "Website": "<a href='http://pirateparty.org.au'>http://pirateparty.org.au</a>"
              },
              "business_number": "99 462 965 754",
              "payment_methods": [
                {
                  "Direct Deposit": [
                    "Name: Pirate Party Australia Incorporated",
                    "BSB: 012084",
                    "Account number: 213142205",
                    "Bank: ANZ"
                  ]
                }, {
                  "Cheque": [
                    "Address:",
                    "Pirate Party Australia",
                    "PO Box 385",
                    "Figtree NSW 2525"
                  ]
                }
              ]
            }


            member_level = "INVALID"
            if member['membership_level'] == "full":
                member_level = "Full Membership"
            elif member['membership_level'] == "associate":
                member_level = "Associate Membership"

            invoice_tmpl = {
                "regarding": member_level,
                "name": "%s %s" % (member['given_names'], member['surname']),
                "reference": invoice['reference'],
                "items": [
                  {
                    "rate_price": invoice['items'][0]['price'],
                    "tax": "0",
                    "description": invoice['items'][0]['item'],
                    "hours_qty": "1"
                  }
                ],
                "already_paid": "0",
                "details": "",
                "address": [
                  member['residential_address'],
                  "%s %s %s" % (member['residential_suburb'],
                      member['residential_state'], member['residential_postcode'])
                ],
                "date": invoice['issued_date'].strftime("%d/%m/%Y"),
                "message": "Thanks for joining Pirate Party Australia!",
                "payment_due": invoice['due_date'].strftime("%d/%m/%Y")
            }

            pdf_data = Invoice(invoice_tmpl, personal).to_pdf()
            try:
                sendmail(create_email(
                    frm="membership@pirateparty.org.au",
                    to="%s %s <%s>" % (member['given_names'], member['surname'], member['email']),
                    subject="Pirate Party Membership Invoice (%s)" % invoice_tmpl['reference'],
                    text=self.invoice_email.format(name=member['given_names'].split(" ")[0]),
                    attachments=[create_attachment("ppau-invoice.pdf", pdf_data)]
                ))
                return invoice
            except Exception as e:
                logging.error("Failed to send invoice - %s" % e)
            return None
        else:
            raise Exception("How did you even get here?")

    def send_admin_message(self, member_record):
        member = member_record['details']
        msg = "New %s member: %s %s [%s] (%s)" % (member['membership_level'], member['given_names'],
                member['surname'], member['email'], member['residential_state'])
        id = member_record['_id'].hex

        sendmail(create_email(
                frm='secretary@pirateparty.org.au',
                reply_to=member['email'],
                to='secretary@pirateparty.org.au',
                subject=msg,
                text="%s\n%s\n$%s" % (id, member_record['invoices'][0]['payment_method'],
                    member_record['invoices'][0]['items'][0]['price']/100)
        ))
        logging.info("New member: %s %s" % (msg, id))
        logging.debug(dumps(member_record, indent=2))

    def send_confirmation(self, member_record):
        member = member_record['details']
        sendmail(create_email(
            frm="membership@pirateparty.org.au",
            to="%s %s <%s>" % (member['given_names'], member['surname'], member['email']),
            subject="Welcome to Pirate Party Australia!",
            text=self.welcome_email.format(name=member['given_names'].split(" ")[0])
        ))

    def get(self):
        self.render(self.name + '.html', record=None)

    def post(self):
        data = self.validate(self.get_argument('data', None))
        member_record = self.create_member_record(data)
        invoice_record = self.create_and_send_invoice(member_record['details'], member_record['invoices'][0])
        if not invoice_record:
            raise HTTPError(500, "invoice failed to send")

        member_record['invoices'][0] = invoice_record
        if not safe_insert(db.members, member_record):
            raise HTTPError(500, "mongodb keeled over")

        self.send_confirmation(member_record)
        self.send_admin_message(member_record)

    def _get_counter(self, name):
        record = safe_modify(db.counters, {"_id": name}, {"$inc": {"count": 1}}, True)
        logging.info("Current count for '%s': %s" % (name, record['count']))
        if record != False:
            return record['count']

class UpdateMemberFormHandler(NewMemberFormHandler):
    def validate(self, data):
        try:
            form_data = json.loads(data)
        except:
            raise HTTPError(400, "invalid form data")

        cleaned = {}
        mandatory_fields = [
            'given_names', 'surname', 'date_of_birth', 'residential_address',
            'residential_postcode', 'residential_state', 'residential_suburb',
            'email', 'primary_phone'
        ]

        optional_fields = [
            'gender', 'postal_address', 'postal_postcode', 'postal_state',
            'postal_suburb', 'secondary_phone'
        ]

        for field in mandatory_fields:
            if field in form_data.keys():
                cleaned[field] = form_data[field]
            else:
                raise HTTPError(400, "missing fields: %s" % field)

        for field in optional_fields:
            if field in form_data.keys():
                cleaned[field] = form_data[field]

        try:
            cleaned['date_of_birth'] = datetime.datetime.strptime(cleaned['date_of_birth'], "%d/%m/%Y")
        except:
            raise HTTPError(400, "invalid form data")

        return cleaned

    def send_admin_message(self, member_record):
        member = member_record['details']
        msg = "[Audit] Updated member: %s %s [%s] (%s)" % (member['given_names'],
                member['surname'], member['email'], member['residential_state'])
        id = member_record['_id'].hex

        sendmail(create_email(
                frm='secretary@pirateparty.org.au',
                reply_to=member['email'],
                to='secretary@pirateparty.org.au',
                subject=msg,
                text=id
        ))
        logging.info("%s %s" % (msg, id))
        logging.debug(dumps(member_record, indent=2))

    def merge_data(self, data, member_record):
        history = {
            "v": 1,
            "details": data,
            "ts": datetime.datetime.utcnow(),
            "action": "update"
        }
        member_record['history'].append(history)

        for k, v in data.items():
            member_record['details'][k] = v

        return member_record

    def get(self, id):
        # 30 minutes max age.
        cookie = self.get_secure_cookie('member_id', max_age_days=0.0208333)

        # Cookie time yeah yeah
        if cookie is None:
            # Let's try to auth.
            self.render(self.name + '-challenge.html', error=False)
            return

        # Check cookie actually matches requested id.
        if cookie.decode() != id:
            self.clear_cookie('member_id')
            logging.error("Attempt to access other member. Cookie: '%s'; Attempt: '%s'" % (
                cookie.decode(), id))
            self.write('')
            return

        # Getting to this point assumes authentication went well.

        id = uuid.UUID(cookie.decode())
        record = db.members.find_one({"_id": id})

        if record['details']['membership_level'] not in ('founder', 'full', 'associate'):
            self.render(self.name + '-challenge.html', error=False)
            return

        self.render(self.name + ".html", record=dumps(record['details']))

    def post(self, id):
        # 30 minutes max age.
        cookie = self.get_secure_cookie('member_id', max_age_days=0.0208333)

        # Cookie time yeah yeah
        if id is not None and cookie is not None and cookie.decode() == id:
            return self.post_update(id)
        else:
            return self.post_attempt_authentication(id)

    def post_attempt_authentication(self, id):
        """This is a plain old POST."""
        try:
            id = uuid.UUID(id)
        except:
            raise HTTPError(400, "invalid ID")

        data_dob = self.get_argument('auth_dob', None)
        data_surname = self.get_argument('auth_surname', None)

        if data_dob is None or data_surname is None:
            return self.render(self.name + "-challenge.html", error=True)

        member_record = db.members.find_one({"_id": id})
        if not member_record:
            logging.error("Attempt to retrieve record for '%s' but none found!" % id.hex)
            return self.render(self.name + "-challenge.html", error=True)

        if member_record['details']['membership_level'] not in ('founder', 'full', 'associate'):
            self.render(self.name + '-challenge.html', error=False)
            return

        if member_record['details']['surname'].lower() == data_surname.lower() and\
                member_record['details']['date_of_birth'].strftime("%d/%m/%Y") == data_dob:
            self.set_secure_cookie("member_id", id.hex)
            return self.render(self.name + ".html", record=dumps(member_record['details']))

        return self.render(self.name + "-challenge.html", error=True)

    def post_update(self, id):
        """This one only works sanely via XHR."""
        try:
            id = uuid.UUID(id)
        except:
            raise HTTPError(400, "invalid ID")

        member_record = db.members.find_one({"_id": id})
        if not member_record:
            raise HTTPError(403, "no member found by id")

        data = self.validate(self.get_argument('data', None))

        # Add audit stuff here too
        member_record['details']['last_audit_confirmation'] = datetime.datetime.utcnow()

        member_record = self.merge_data(data, member_record)

        if not safe_modify(db.members, {"_id": id}, member_record):
            raise HTTPError(500, "mongodb keeled over on update")

        self.send_admin_message(member_record)
        self.clear_cookie('member_id')


class PaymentMethodFormHandler(NewMemberFormHandler):
    def validate(self, data):
        try:
            form_data = json.loads(data)
        except:
            raise HTTPError(400, "invalid form data")

        cleaned = {}
        mandatory_fields = ['payment_method', 'submission']

        for field in mandatory_fields:
            if field in form_data.keys():
                cleaned[field] = form_data[field]
            else:
                raise HTTPError(400, "missing fields: %s" % field)

        return cleaned

    def send_admin_message(self, member_record):
        member = member_record['details']
        msg = "Payment method selected: %s %s [%s] (%s)" %\
                (member['given_names'], member['surname'],
                 member['email'], member['residential_state'])
        id = member_record['_id'].hex

        sendmail(create_email(
                frm='secretary@pirateparty.org.au',
                to='secretary@pirateparty.org.au',
                subject=msg,
                text="%s\n%s" % (id, member_record['invoices'][0]['payment_method'])
        ))
        logging.info("%s %s" % (msg, id))
        logging.debug(dumps(member_record, indent=2))

    def merge_data(self, data, member_record):
        invoice = self.create_invoice_record("full", data['payment_method'])
        history_item = {
            "action": "new-invoice",
            "ts": invoice['ts'],
            "invoice": invoice,
            "v": 1
        }
        member_record['history'].append(history_item)
        member_record['invoices'].append(invoice)
        return member_record

    def send_confirmation(self, member_record):
        member = member_record['details']
        sendmail(create_email(
            frm="membership@pirateparty.org.au",
            to="%s %s <%s>" % (member['given_names'], member['surname'], member['email']),
            subject="Welcome to Pirate Party Australia!",
            text=self.welcome_email.format(name=member['given_names'].split(" ")[0])
        ))

    def get(self, id):
        try:
            id = uuid.UUID(id)
        except:
            self.render(self.name + "-notfound.html")
            return

        record = db.members.find_one({"_id": id})
        if not record:
            self.render(self.name + "-notfound.html")
            return
        if record['details']['membership_level'] in ('resigned', 'expelled'):
            return self.write("You have already resigned from the Party!")

        invoices = record.get('invoices')
        if record['details']['membership_level'] != "full" or (invoices is not None and len(invoices) > 0):
            self.render(self.name + "-paymentselected.html")
            return

        self.render(self.name + ".html", record=None)

    def post(self, id):
        try:
            id = uuid.UUID(id)
        except:
            raise HTTPError(400, "invalid ID")

        member_record = db.members.find_one({"_id": id})
        if not member_record:
            raise HTTPError(403, "no member found by id")

        invoices = member_record.get('invoices')
        if invoices is not None and len(invoices) > 0:
            raise HTTPError(403, "payment method already chosen")

        data = self.validate(self.get_argument('data', None))
        member_record = self.merge_data(data, member_record)
        invoice_record = self.create_and_send_invoice(member_record['details'], member_record['invoices'][0])
        if not invoice_record:
            raise HTTPError(500, "invoice failed to send")

        member_record['invoices'][0] = invoice_record
        if not safe_modify(db.members, {"_id": id}, member_record):
            raise HTTPError(500, "mongodb keeled over on update")

        self.send_confirmation(member_record)
        self.send_admin_message(member_record)

class AuditHandler(RequestHandler):
    def _is_already_done(self, record):
        return record['details'].get('last_audit_confirmation', None) is not None

    def _modify_record(self, record):
        record['details']['last_audit_confirmation'] = datetime.datetime.utcnow()

    def _get_msg(self, member):
        msg = "[Audit] Member confirmed: %s %s [%s] (%s)" % (member['given_names'],
                member['surname'], member['email'], member['residential_state'])
        return msg

    def _page_msg(self):
        return 'Thanks for confirming your membership! You may now close this window.'

    def send_admin_message(self, member_record):
        member = member_record['details']
        id = member_record['_id'].hex

        msg = self._get_msg(member)

        sendmail(create_email(
                frm='secretary@pirateparty.org.au',
                to='secretary@pirateparty.org.au',
                subject=msg,
                text=id
        ))
        logging.info("%s %s" % (msg, id))

    def get(self, id):
        try:
            id = uuid.UUID(id)
        except:
            return self.write('')

        record = db.members.find_one({"_id": id})
        if not record:
            return self.write('')

        if record['details']['membership_level'] not in ('full', 'founder'):
            return self.write("")

        # You are annoying and you know who you are.
        if self._is_already_done(record):
            return self.write("You have already confirmed your membership.")

        self._modify_record(record)

        if not safe_modify(db.members, {"_id": id}, record):
            raise HTTPError(500, "mongodb keeled over on update")

        self.send_admin_message(record)
        self.write(self._page_msg())

class NSWAuditHandler(AuditHandler):
    def _is_already_done(self, record):
        return record['details'].get('nsw_membership_form_mailed', None) is not None

    def _modify_record(self, record):
        record['details']['nsw_membership_form_mailed'] = datetime.datetime.utcnow()

    def _get_msg(self, member):
        msg = "[NSW] Member mailed: %s %s [%s]" % (member['given_names'],
                member['surname'], member['email'])
        return msg

    def _page_msg(self):
        return 'Thanks for mailing your form! You may now close this window.'

class ResignHandler(RequestHandler):
    def get(self, id):
        try:
            id = uuid.UUID(id)
        except:
            return self.write('')

        record = db.members.find_one({"_id": id})
        if not record:
            return self.write('')

        if record['details']['membership_level'] in ('resigned', 'expelled'):
            return self.write("You have already resigned from the Party!")

        self.render('resign.html')

    def post(self, id):
        try:
            id = uuid.UUID(id)
        except:
            return self.write('')

        record = db.members.find_one({"_id": id})
        if not record:
            return self.write('')

        if record['details']['membership_level'] in ('expelled', 'resigned'):
            return self.write('')

        reason = self.get_argument('reason', '')

        # TODO this state shouldn't be added to the details, but in history
        record['details']['membership_level'] = "resigned"
        record['details']['resigned_on'] = datetime.datetime.utcnow()

        if not safe_modify(db.members, {"_id": id}, record):
            raise HTTPError(500, "mongodb keeled over on update")

        self.send_admin_message(record, reason)

        # Just in case they updated then resigned, and want to mangle data.
        self.clear_cookie('member_id')

        self.write("Sorry to see you go! You may now close this window.")

    def send_admin_message(self, member_record, reason):
        member = member_record['details']
        msg = "[Audit] Member resigned: %s %s [%s] (%s)" % (member['given_names'],
                member['surname'], member['email'], member['residential_state'])
        id = member_record['_id'].hex

        sendmail(create_email(
                frm='secretary@pirateparty.org.au',
                to='secretary@pirateparty.org.au',
                subject=msg,
                text="%s\n\n%s" % (id, reason)
        ))
        logging.info("%s %s" % (msg, id))



if __name__ == "__main__":
    tornado.options.parse_command_line()
    if options.no_mailer:
        from mock import Mock
        sendmail = Mock()

    with open('config.json') as f:
        conf = json.load(f)

    application = Application([
            (r"/", NewMemberFormHandler),
            (r"/update/(.*)", UpdateMemberFormHandler),
            #(r"/payment/(.*)", PaymentMethodFormHandler),
            (r"/audit/(.*)", AuditHandler),
            (r"/nsw/(.*)", NSWAuditHandler),
            (r"/resign/(.*)", ResignHandler),
            (r"/static/(.*)", StaticFileHandler, {"path": "../static"})
        ],
        cookie_secret=conf['cookie_secret'],
        **{"template_path": "../templates"})
    application.listen(options.port, xheaders=True)
    tornado.ioloop.IOLoop.instance().start()

