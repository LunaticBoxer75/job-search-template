"""Generate a JavaScript bookmarklet that auto-fills ATS application forms.

When the user clicks the bookmarklet on a Workday/Greenhouse/Lever/iCIMS form, it
fills ONLY unambiguous identity/contact/education fields.

Deliberately never filled — she answers these herself, because a wrong
auto-answer costs more than typing it: work authorization, visa sponsorship,
EEO / demographics, salary, start date. (An earlier version matched by substring,
so "Ethnicity" got "Seattle" and "legally authorized to work in the United States?"
got "WA".)
"""

import json
import urllib.parse
from pathlib import Path

from .profile import identity, load as load_profile

CONFIG_DIR = Path(__file__).parent.parent / "config"


def get_profile_data():
    """Values the bookmarklet may fill. Nothing visa/EEO/salary-related."""
    with open(CONFIG_DIR / "profile.json") as f:
        profile = json.load(f)
    candidate = profile.get("candidate", {})
    full = candidate["name"]
    return {
        "firstName": full.split()[0],
        "lastName": full.split()[-1],
        "fullName": full,
        "email": candidate.get("email", ""),
        "phone": candidate.get("phone", ""),
        "linkedin": f"https://www.linkedin.com/in/{candidate.get('linkedin', '')}/",
        "city": identity()["home_city"],
        "school": candidate.get("university", ""),
        "degree": identity().get("degree", ""),
    }


# Answers she should give by hand (shown on the Tools page, never auto-filled).
# Answers she should give by hand (shown on the Tools page, never auto-filled).
MANUAL_ANSWERS = load_profile().get("manual_answers", {})

_JS = r"""
(function(){
  var D = %(data)s;
  var SENSITIVE = /sponsor|authori[sz]|visa|citizen|immigra|gender|sex\b|race|ethnic|hispanic|latino|veteran|disab|pronoun|salary|compensation|pay\b|relocat|start date|emergency|reference|company|employer|manager|supervisor|previous|former/;
  function tokens(s){ return (s||'').replace(/([a-z])([A-Z])/g,'$1 $2').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean); }
  function has(t, w){ return t.indexOf(w) !== -1; }
  function pick(el){
    var ac = (el.getAttribute('autocomplete')||'').toLowerCase();
    var t = tokens(el.name).concat(tokens(el.id));
    var lbl = ((el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('aria-label') || '').toLowerCase();
    if (SENSITIVE.test(lbl) || SENSITIVE.test(t.join(' '))) return null;
    var lt = tokens(lbl);
    if (ac === 'given-name' || (has(t,'first') && has(t,'name')) || has(t,'fname') || has(t,'givenname')) return D.firstName;
    if (ac === 'family-name' || (has(t,'last') && has(t,'name')) || has(t,'lname') || has(t,'surname') || has(t,'familyname')) return D.lastName;
    if (ac === 'email' || has(t,'email') || has(lt,'email')) return D.email;
    if (ac.indexOf('tel') === 0 || has(t,'phone') || has(t,'mobile') || has(lt,'phone')) return D.phone;
    if (has(t,'linkedin') || has(lt,'linkedin')) return D.linkedin;
    if (ac === 'address-level2' || (has(t,'city') && t.length <= 3)) return D.city;
    if (has(t,'school') || has(t,'university') || has(t,'institution')) return D.school;
    if (has(t,'degree')) return D.degree;
    if (ac === 'name' || (t.length === 1 && t[0] === 'name') || (has(t,'full') && has(t,'name'))) return D.fullName;
    return null;
  }
  function setVal(el, v){
    var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    ['input','change','blur'].forEach(function(e){ el.dispatchEvent(new Event(e, {bubbles:true})); });
  }
  var filled = 0;
  document.querySelectorAll('input, textarea').forEach(function(el){
    var type = (el.type||'text').toLowerCase();
    if (['hidden','radio','checkbox','file','submit','button','password'].indexOf(type) !== -1) return;
    if (el.value || el.disabled || el.readOnly || el.offsetParent === null) return;
    var v = pick(el);
    if (v) { setVal(el, v); filled++; }
  });
  alert('Auto-Fill: filled ' + filled + ' fields.\n\nNOT filled on purpose: sponsorship, work authorization, EEO, salary, start date. Answer those yourself. Review everything before submitting.');
})();
"""


def generate_bookmarklet_js():
    """Returns (javascript: URL, readable source). The source has no // comments, so
    it survives being minified onto one line."""
    source = _JS % {"data": json.dumps(get_profile_data())}
    minified = " ".join(source.split())
    encoded = "javascript:" + urllib.parse.quote(minified, safe="(){}[],;:.!?'+=&|<>%@/*$^\\")
    return encoded, source


def get_bookmarklet_html():
    """Return HTML snippet with the draggable bookmarklet link."""
    encoded, _ = generate_bookmarklet_js()
    return f'<a href="{encoded}" class="bookmarklet">📌 Auto-Fill</a>'
