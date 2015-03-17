from mako.lookup import TemplateLookup
import tempfile
import os
import subprocess
import logging

tl = TemplateLookup(directories=['.'])

class Invoice:
    def __init__(self, invoice, personal, template=None):
        self.template = template or tl.get_template('default-au.tpl')
        self.data = invoice
        self.biller = personal

    def to_html(self):
        return self.template.render(
            invoice=self.data,
            personal=self.biller
        )

    def to_pdf(self):
        html = self.to_html()
        try:
            tmp = tempfile.NamedTemporaryFile('w', delete=False)
            tmp.write(html)
            tmp.close()
            os.rename(tmp.name, tmp.name + '.html')
            res = subprocess.call(['wkhtmltopdf', '--print-media-type', tmp.name + '.html', tmp.name + ".pdf"])
            if res != 0:
                logging.warn("Result of wkhtmltopdf was errno %s." % res)
            pdff = open(tmp.name + '.pdf', 'rb')
            pdf = pdff.read()
            pdff.close()

            os.unlink(tmp.name + '.html')
            os.unlink(tmp.name + '.pdf')

            return pdf
        except IOError as e:
            logging.error(e)

def test():
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
          "BSB: xxxxxx",
          "Account number: xxxxxxxxx"
        ]
      }
    ],
  }

  invoice = {
      "regarding": "Membership",
      "name": "John Smith",
      "reference": "0001",
      "items": [
        {
          "rate_price": "20",
          "tax": "0",
          "description": "Full Membership",
          "hours_qty": "1"
        }
      ],
      "already_paid": "0",
      "details": "",
      "template": "default-au.tpl",
      "address": [
        "123 Fake Street",
        "Derpton NSW 2999"
      ],
      "date": "2013-02-17",
      "message": "Thanks for joining Pirate Party Australia!",
      "payment_due": "Upon receipt"
  }

  inv = Invoice(invoice, personal).to_html()
  print(inv)

if __name__ == "__main__":
    test()
