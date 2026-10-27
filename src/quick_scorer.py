"""Fast rule-based scorer (Pass 1) for bulk job scoring.

Scores every job from title, company, location, posting date and (when present)
description. It must carry the ~65% of rows that have NO description (all LinkedIn
rows), so the title/company rules do the heavy lifting and description rules only
refine Indeed rows.

Score = role + (role / 50) x (seniority fit + location + employer H-1B strength
        + freshness + description signals) + penalties, then hard caps.
Role relevance gates the rest, so a borderline title with perfect context cannot outrank
a core title with average context. Role families, their priority, the home area and the
description keywords come from config/profile.json ("scoring"), so the same rules can
serve another candidate. Caps remove what the candidate cannot take or should not see near
the top: the `excluded_fields` (clinical, food service, retail store, ...); when
`needs_visa_sponsorship` is set, security clearance, U.S.-person (ITAR) or citizen-only
roles and federal agencies; director-and-above; senior / manager titles filed under
internships. Scores carry one decimal so the top band ranks.

Every keyword rule uses word boundaries (a plain substring once matched "intern"
inside "Internal" and mis-tagged 1,323 jobs).

The inline Claude scorer (Pass 2) is still what creates trustworthy ordering for
the rows it reads; this pass decides which rows it reads.
"""

import json
import math
import re
from datetime import date, datetime

from . import profile
from .locations import metro_of
from .scraper import get_db
from .sponsor_data import _load_verified


_CFG = profile.scoring()
CANDIDATE_YEARS = profile.years()
_NAME = profile.first_name()
_EXCLUDED = set(_CFG.get("excluded_fields", []))
_VISA = _CFG.get("needs_visa_sponsorship", False)


def _words(*alts):
    """Case-insensitive alternation anchored on word boundaries at both ends."""
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.I)


# --------------------------------------------------------------------------------------
# Title rules
# --------------------------------------------------------------------------------------

# Hard exclusions (capped in _role_points). A job matching one is not a target no matter
# what else the title says ("Podiatrist (Program Manager)").
_CLINICAL = _words(
    r"nurses?", r"nursing", r"rn", r"lpn", r"lvn", r"cna", r"crna", r"physicians?", r"doctor",
    r"surgeons?", r"podiatr\w*", r"dentists?", r"dental", r"hygienists?", r"orthodont\w*",
    r"pharmacists?", r"pharm ?techs?", r"pharmacy (?:techs?|technicians?|manager|intern)",
    r"therapists?", r"phlebotom\w*", r"radiolog\w*", r"sonograph\w*", r"ultrasound",
    r"mri", r"x-ray", r"psychiatr\w*", r"psycholog\w*", r"counselors?", r"clinicians?",
    r"social workers?", r"caregivers?", r"home health", r"medical assistants?",
    r"physician assistant", r"pa-c", r"veterinar\w*", r"vet techs?", r"optometr\w*", r"opticians?",
    r"chiropract\w*", r"audiolog\w*", r"dietitians?", r"nutritionists?", r"paramedics?", r"emts?",
    r"midwi\w*", r"anesthe\w*", r"behavior analysts?", r"bcba", r"rbt",
    r"behavior(?:al)? technicians?", r"medical cod\w*", r"risk adjustment",
    r"coding (?:operations|specialist|auditor|educator|quality|validation|analyst|coordinator)",
    r"hospice", r"icu", r"operating room", r"cath lab",
    r"patient (?:care|access|services)", r"care (?:coordinator|manager|navigator)", r"case managers?",
    r"sterile processing", r"surgical techs?", r"medical records?", r"health information management",
    r"epic (?:analyst|configuration|certified|application)",
)
# Healthcare-delivery context: excluded unless the title is plainly a target function
# ("Clinical Supply Chain Manager" stays, "Outpatient Mental Health Program Manager" goes).
_HEALTH_CONTEXT = _words(
    r"clinical", r"clinics?", r"patients?", r"medical", r"hospital", r"outpatient", r"inpatient",
    r"mental health", r"behavioral health", r"surgical", r"surgery", r"pharmacy",
    r"laborator(?:y|ies)", r"lab", r"memory care", r"senior living", r"assisted living",
    r"residential", r"wellness", r"rehab\w*", r"imaging", r"respiratory", r"pediatric\w*",
    r"emergency department", r"health services", r"therapy", r"oncology", r"cardiolog\w*", r"dialysis",
)
_FOOD_FRONTLINE = _words(
    r"cooks?", r"line cook", r"prep cook", r"chefs?", r"sous", r"baristas?", r"bartenders?",
    r"dishwashers?", r"bussers?", r"(?:food|banquet|cocktail|restaurant) servers?", r"crew members?",
    r"cashiers?", r"food service (?:worker|associate|assistant)", r"baker", r"meat cutter", r"butcher",
)
_FOOD_CONTEXT = _words(
    r"food ?services?", r"food (?:and|&) beverage", r"f&b", r"restaurants?", r"kitchen", r"catering",
    r"cafeteria", r"dining", r"concessions?", r"vending", r"hospitality", r"hotel", r"housekeep\w*",
    r"front desk", r"guest services", r"banquets?", r"culinary", r"bakery", r"deli", r"pizza",
)
_RETAIL_STORE = _words(
    r"sales associates?", r"store associates?", r"retail associates?", r"retail sales",
    r"store (?:manager|associate|lead|supervisor|team|clerk|stocker|keyholder|leader|director)",
    r"assistant (?:store|station|department) manager", r"department manager", r"key ?holder",
    r"stockers?", r"merchandisers?", r"visual merchandis\w*",
    r"retail (?:associate|consultant|specialist|team|store|supervisor|lead|operations team)",
    r"team members?", r"shift (?:lead|leader|supervisor|manager)", r"station manager",
    r"customer service (?:representative|associate|rep)", r"brand ambassador", r"beauty advisor",
    r"sales (?:representative|consultant|trainee)",
)
_DRIVING = _words(
    r"drivers?", r"driving", r"cdl", r"truck ?drivers?", r"couriers?", r"chauffeurs?",
    r"delivery (?:associate|specialist|driver|helper|expert|professional|representative)",
    r"route (?:sales|driver|representative)", r"vehicle operators?", r"operators?", r"forklift",
)
_WAREHOUSE_LABOR = _words(
    r"warehouse (?:associate|worker|clerk|operative|handler|team member|specialist|lead|laborer|technician)",
    r"materials? handlers?", r"pickers?", r"packers?", r"order (?:selector|picker|filler)s?",
    r"loaders?", r"unloaders?", r"package handlers?", r"fulfillment (?:associate|center associate)",
    r"stock (?:associate|clerk|handler)", r"inventory (?:associates?|clerk|counter)",
    r"shipping (?:and|&) receiving", r"receiving (?:clerk|associate)", r"shipping clerk",
    r"logistics center associate", r"dock (?:worker|associate)", r"yard (?:driver|jockey|spotter|hostler)",
    r"sorters?", r"general labou?r(?:er)?", r"labou?rers?", r"production (?:associate|worker)",
    r"freight (?:flow|handler|associate)", r"warehouse auditors?", r"overnight",
    r"assemblers?",
)
_TRADES = _words(
    r"technicians?", r"(?:pharm|pharmacy|lab|vet|surgical|maintenance|field|service|supply chain|hvac|sterile) techs?",
    r"mechanics?", r"electricians?", r"plumbers?", r"welders?", r"machinists?", r"carpenters?",
    r"painters?", r"hvac", r"millwrights?", r"installers?",
    r"maintenance (?:technician|mechanic|worker|manager|supervisor|associate|lead|intern|planner)",
    r"inspectors?", r"line(?:man|men|worker)", r"custodians?", r"custodial", r"janitor\w*",
    r"groundskeepers?", r"landscap\w*", r"security (?:officers?|guards?)", r"lifeguards?",
    r"cleaners?", r"porters?", r"roofers?", r"masons?", r"diesel",
)
_UNRELATED = _words(
    r"teachers?", r"teaching", r"professors?", r"faculty", r"lecturers?", r"instructors?", r"tutors?",
    r"para ?educators?", r"paraprofessional", r"child ?care", r"nanny", r"preschool", r"daycare",
    r"youth", r"camp", r"coach(?:es)?", r"pastor", r"ministers?", r"ministry", r"chaplain",
    r"worship", r"librarian", r"stylists?", r"barbers?", r"cosmetolog\w*", r"estheticians?",
    r"massage", r"groomers?", r"fitness", r"personal trainer", r"lawyer", r"attorneys?", r"counsel",
    r"paralegal", r"real estate agent", r"leasing (?:agent|consultant|specialist)",
    r"property manager", r"loan officer", r"mortgage", r"insurance agent", r"recruiters?",
    r"sourcers?", r"talent (?:acquisition|sourcing)", r"recruiting", r"media (?:buyer|planner|planning)",
    r"(?:event|events|wedding|financial|travel|meeting|estate) planners?",
    r"admin(?:istrative)? (?:support|assistants?|coordinators?|specialists?)", r"office (?:manager|assistant|coordinator)s?",
    r"receptionists?", r"executive assistants?", r"clerks?", r"data entry",
)
# Engineering / science / IT: not her field. "Hard" titles are capped; "soft" IT/security
# titles only drop in role points (security *compliance* work is adjacent to hers).
_TECH_HARD = _words(
    r"engineers?", r"developers?", r"programmers?", r"sde", r"swe", r"software", r"firmware",
    r"devops", r"sre", r"scientists?", r"machine learning", r"applied science", r"architects?",
    r"full[- ]?stack", r"front[- ]?end", r"back[- ]?end", r"sdet", r"propulsion", r"avionics",
    r"biologists?", r"chemists?", r"physicists?", r"geologists?", r"actuar\w*", r"statisticians?",
    r"drafter", r"designers?", r"ux", r"ui",
)
# ...checked on the whole title (a security or IT domain anywhere makes it a tech role)
_TECH_SOFT_ANY = _words(
    r"it", r"help ?desk", r"desktop support", r"systems? administrator", r"sysadmin",
    r"network (?:administrator|analyst|security|operations)", r"cyber\w*", r"information security",
    r"infosec", r"security", r"database", r"dba", r"technical",
)
# ...checked on the head segment only (team names like "..., NASC Engineering" are fine)
_TECH_SOFT_HEAD = _words(
    r"engineering", r"quant", r"quantitative", r"sap", r"oracle", r"erp", r"dynamics 365", r"d365",
    r"workday", r"plm", r"salesforce", r"servicenow", r"netsuite", r"mes",
)
_TPM_PHRASE = re.compile(r"\btechnical (?:program|project) (?:manager|management|mgr)s?\b|\btpm\b", re.I)
# Technical PM roles are kept but ranked below every non-technical PM / ops role (user, 2026-09-27:
# "technical roles are not the highest priority, eg: PM is way above TPM").
_TECHNICAL_PM = re.compile(
    r"\btechnical (?:program|project|product) (?:manager|management|mgr|lead|owner)s?\b|\btpms?\b"
    r"|\b(?:program|project|product) (?:manager|management|mgr|lead)s?\s*[,(\-–|]\s*technical\b", re.I)
TECHNICAL_PM_CAP = _CFG.get("technical_pm_cap")  # null in the profile = no cap
# Function mismatch: finance / HR / marketing / sales / legal. Not excluded (a "Supply Chain
# Financial Analyst" is borderline-useful) but they cannot reach the top band.
_FINANCE_FUNCTION = _words(
    r"accountants?", r"accounting", r"bookkeep\w*", r"financial(?!\s+crimes?)", r"finance", r"fp&a",
    r"tax", r"treasury", r"payroll", r"billing", r"accounts (?:payable|receivable)", r"controller",
    r"investment", r"banking", r"underwrit\w*", r"wealth", r"portfolio",
)
_OTHER_FUNCTION = _words(
    r"human resources", r"hr", r"talent", r"people (?:operations|partner|development|ops)",
    r"benefits", r"compensation", r"learning (?:and|&) development", r"training", r"marketing",
    r"brand", r"social media", r"communications", r"public relations", r"creative", r"copywrit\w*",
    r"seo", r"advertising", r"media", r"sales", r"account executive", r"account manager",
    r"business development", r"customer success", r"partnerships?", r"revenue", r"go[- ]to[- ]market",
    r"gtm", r"legal", r"localization", r"culture", r"diversity", r"dei", r"skilling",
    r"social impact", r"philanthrop\w*", r"fundrais\w*", r"grants?", r"environmental", r"ehs",
    r"construction", r"facilities", r"real estate", r"headcount", r"people analytics",
    r"workforce planning", r"claims", r"solutions? consultants?", r"pre-?sales",
)
# F-1 blockers in the title itself.
_TITLE_CLEARANCE = _words(
    r"clearance", r"cleared", r"ts ?/ ?sci", r"top secret", r"secret", r"polygraph", r"itar",
    r"dod", r"defen[cs]e", r"u\.?s\.? citizens?(?:hip)?", r"usc only", r"citizens? only",
)
# "Fraud Defense Analyst" / "Cyber Defense" are not defense-industry roles.
_TITLE_DEFENSE_OK = _words(r"(?:fraud|cyber|threat|loss|abuse|risk|brand|legal|claims) defen[cs]e")
_TITLE_GOVERNMENT = _words(
    r"federal", r"govcon", r"government", r"faa", r"nasa", r"navy", r"army", r"air force", r"usaf",
    r"marine corps", r"dhs", r"public trust", r"skillbridge",
    r"military (?:development|program|fellowship|transition|spouse)", r"veterans? (?:program|fellowship|hiring)",
)
_TITLE_TARGET_CORE = _words(*_CFG["core_target_terms"])

# Positive role families: (points, label, regex). The best-scoring match wins.
_FAMILIES = [(f["points"], f["label"], _words(*f["title_patterns"])) for f in _CFG["role_families"]]
_TOP_FAMILIES = set(_CFG["top_families"])
# Risk roles outside her fraud / operational / supply-chain risk lane: finance-quant risk,
# insurance loss control, workplace-safety and clinical risk.
_RISK_OFFDOMAIN = _words(
    r"credit", r"market", r"model", r"quantitative", r"actuarial", r"insurance", r"underwriting",
    r"clinical", r"patient", r"healthcare", r"safety", r"ehs", r"osha", r"environmental", r"whs",
    r"recordkeeping", r"loss control", r"risk control", r"claims", r"capital",
)
_COMPLIANCE_OFFDOMAIN = _words(
    r"environmental", r"ehs", r"safety", r"building", r"construction", r"permits?", r"osha", r"fire",
    r"housing", r"tax", r"payroll", r"coding", r"clinical", r"hipaa", r"medicare", r"medicaid",
    r"lihtc", r"fair housing", r"section 8", r"hud", r"residential", r"property", r"leasing",
)

# Seniority
_EXEC = _words(
    r"directors?", r"dir", r"vp", r"svp", r"evp", r"vice president", r"head", r"chief", r"cfo",
    r"coo", r"cto", r"ceo", r"cpo", r"cio", r"ciso", r"president", r"principal", r"distinguished",
    r"general manager", r"managing director", r"executive director",
    r"(?:managing|consulting|equity|senior) partner", r"^partner",
    r"site (?:leader|lead|director|manager)", r"plant manager", r"country manager",
)
_SENIOR = _words(r"senior", r"sr", r"snr", r"avp", r"group manager")
_STAFF_SENIOR = re.compile(r"\bstaff\b.*\b(?:manager|tpm|program|product|engineer|scientist|architect)\b"
                           r"|\b(?:manager|program|product)\b.*\bstaff\b", re.I)
_MANAGER = _words(r"managers?", r"mgr", r"tpm", r"pm")
_PGM_TITLE = _words(r"program managers?", r"project managers?", r"product managers?", r"tpm", r"pm",
                    r"program mgr", r"project mgr", r"product mgr")
_ASSIST_MGR = re.compile(r"\b(?:assistant|associate|asst|assoc)\b(?:\s+\w+){0,2}\s+(?:manager|mgr)\b", re.I)
_LEAD = _words(r"lead", r"supervisor", r"supervising", r"foreman")
_ENTRY = _words(r"junior", r"jr", r"entry[- ]level", r"entry", r"new grad\w*", r"graduate", r"trainee",
                r"apprentice\w*", r"early career", r"associate")
_LEVEL_NUM = re.compile(
    r"\b(?:manager|mgr|analyst|specialist|buyer|planner|associate|coordinator|consultant|agent|"
    r"officer|auditor|accountant|administrator|representative|tpm|investigator|level|grade|or)"
    r"\s+(i{1,3}|iv|v|[1-5])\b", re.I)
_IC_LEVEL = re.compile(r"\bic ?([1-6])\b", re.I)  # Oracle-style IC1..IC6
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5}
_INTERN_TITLE = _words(r"interns?", r"internships?", r"co-?op", r"coop", r"apprenticeships?",
                       r"summer (?:analyst|associate)s?", r"student")
_RETURNSHIP = _words(r"return[- ]to[- ]work", r"returnships?", r"re-?entry program", r"career (?:re-?entry|relaunch)")
_CONTRACT_TITLE = re.compile(
    r"\(\s*(?:contract|contractor|temp|temporary|part[- ]time|seasonal|pt|prn|per diem)\b[^)]*\)"
    r"|\b(?:contract[- ]to[- ]hire|temp[- ]to[- ]hire|temporary|seasonal|part[- ]time|per diem|contractor)\b"
    r"|[-|,]\s*contract\s*$", re.I)


# --------------------------------------------------------------------------------------
# Company rules
# --------------------------------------------------------------------------------------

# Employers whose postings (almost) always need clearance / U.S.-person status. Built from
# the Indeed descriptions in data/jobs.db: every name here had >=70% of its readable posts
# carrying an ITAR / clearance / citizenship requirement, plus the defense primes. Applied
# only when the row has no description; when there is one, the description decides.
_DEFENSE_EMPLOYER = _words(
    r"spacex", r"space x", r"blue origin", r"raytheon", r"rtx", r"lockheed", r"northrop",
    r"anduril", r"bae systems", r"booz allen", r"saronic", r"shield ai", r"collins aerospace",
    r"leidos", r"rocket lab", r"rocketdyne", r"aerojet", r"sierra nevada", r"sierra space",
    r"united launch alliance", r"ula", r"saic", r"science applications international", r"caci",
    r"mitre", r"general dynamics", r"peraton", r"mantech", r"huntington ingalls", r"l3harris",
    r"l3 harris", r"pratt (?:&|and) whitney", r"amentum", r"elbit", r"bell textron",
    r"textron systems", r"draper", r"aerospace corporation", r"woodward", r"precision castparts",
    r"serco", r"guidehouse", r"impulse space", r"ursa major", r"cesiumastro", r"firefly aerospace",
    r"true anomaly", r"karman", r"leonardo drs", r"boeing", r"augmentis", r"astrion",
    r"lawrence livermore", r"sandia", r"los alamos", r"national laborator(?:y|ies)",
    r"defen[cs]e",
    r"federal (?:systems|solutions?|services|technologies|group)", r"federal(?=\s*$)",
    r"government (?:services|solutions|systems|tactical)",
    r"cherokee federal",
)
# U.S. federal agencies: competitive-service jobs require U.S. citizenship by law.
_FEDERAL_AGENCY = re.compile(
    r"\b(?:u\.?\s?s\.?|us|united states)\s+(?:department|dept|army|navy|air force|marine|coast guard|"
    r"customs|pacific fleet|fleet forces|government|office of|bureau|forest service|postal)\b"
    r"|\bdepartment of (?:the )?(?:veterans affairs|defense|army|navy|air force|homeland security|energy)\b"
    r"|\bveterans (?:affairs|health)\b|\barmy (?:reserve|national guard|corps)\b|\bnavy region\b"
    r"|\bnational guard\b|\bunited states postal service\b|\busps\b|\bfederal aviation administration\b"
    r"|\bcustoms and border protection\b|\btransportation security administration\b"
    r"|\b(?:u\.?\s?s\.?|united states)\s+(?:attorneys?'?|marshals?|courts?|district court|senate|house of representatives|"
    r"mint|secret service|probation|patent|geological survey|census)\b"
    r"|\bgovernment accountability office\b|\blibrary of congress\b|\bsmithsonian\b", re.I)
_AMAZON_LEO = _words(r"amazon leo", r"amz leo", r"leo", r"kuiper")

_CORP_SUFFIX = {"INC", "LLC", "LLP", "LP", "LTD", "LIMITED", "CORP", "CORPORATION", "CO", "COMPANY",
                "PLC", "NA", "THE", "INCORPORATED", "AG", "SA", "GMBH", "US", "USA"}
_GENERIC_FIRST = {
    "GENERAL", "UNITED", "AMERICAN", "NATIONAL", "FIRST", "GLOBAL", "INTERNATIONAL", "NEW", "NORTH",
    "SOUTH", "WEST", "EAST", "SOUTHERN", "NORTHERN", "WESTERN", "EASTERN", "PACIFIC", "ATLANTIC",
    "ADVANCED", "APPLIED", "INTEGRATED", "DIGITAL", "DATA", "SYSTEMS", "TECHNOLOGY", "TECHNOLOGIES",
    "TECH", "SOLUTIONS", "SERVICES", "CONSULTING", "HEALTH", "MEDICAL", "CAPITAL", "BANK", "CITY",
    "STATE", "UNIVERSITY", "INFO", "SMART", "PRIME", "ALPHA", "STAR", "BLUE", "GREEN", "RED", "ROYAL",
    "PREMIER", "ELITE", "METRO", "CENTRAL", "SAINT", "ST", "SAN", "LOS", "LAS", "FORT", "MOUNT",
    "GRAND", "GREAT", "INNOVATIVE", "CREATIVE", "STRATEGIC", "NEXT", "APEX", "SUMMIT", "PINNACLE",
    "VISION", "INSIGHT", "DYNAMIC", "UNIVERSAL", "STANDARD", "AMERICA", "AMERICAS", "CALIFORNIA",
    "TEXAS", "WORLD", "GROUP", "PARTNERS", "ASSOCIATES", "ENTERPRISE", "ENTERPRISES", "INDUSTRIES",
}

_SPONSOR_INDEX = None


def _company_tokens(name):
    toks = re.sub(r"[^A-Z0-9 ]+", " ", (name or "").upper().replace("&", " ")).split()
    return [t for t in toks if t not in _CORP_SUFFIX]


def _sponsor_index():
    """Token-prefix index over USCIS-verified H-1B approvals (config/h1b_sponsors_verified.json)."""
    global _SPONSOR_INDEX
    if _SPONSOR_INDEX is None:
        idx = {}
        for key, val in _load_verified().get("sponsors", {}).items():
            toks = _company_tokens(key)
            n_appr = val.get("h1b_approvals", 0) or 0
            for n in range(1, len(toks) + 1):
                if n == 1 and (toks[0] in _GENERIC_FIRST or len(toks[0]) < 3):
                    continue
                k = tuple(toks[:n])
                idx[k] = max(idx.get(k, 0), n_appr)
            if len(toks) >= 2:  # "WAL MART" -> "WALMART"
                k = (toks[0] + toks[1],)
                idx[k] = max(idx.get(k, 0), n_appr)
        _SPONSOR_INDEX = idx
    return _SPONSOR_INDEX


def _h1b_approvals(company):
    toks = _company_tokens(company)
    if not toks:
        return 0
    idx = _sponsor_index()
    for n in range(len(toks), 0, -1):
        # A lone first word only identifies short names ("Amazon", "Google Cloud"), not
        # "Meta Financial Group".
        if n == 1 and (len(toks) > 2 or toks[0] in _GENERIC_FIRST or len(toks[0]) < 3):
            continue
        hit = idx.get(tuple(toks[:n]))
        if hit:
            return hit
    return 0


# --------------------------------------------------------------------------------------
# Location rules
# --------------------------------------------------------------------------------------

_HOME = _CFG["home_area"]
_HOME_CORE = _words(*_HOME["core_cities"])
_HOME_NEAR = _words(*_HOME["near_cities"])
HOME_LABELS = (_HOME["core_label"], _HOME["label"])
_IN_HOME_STATE = re.compile(
    rf"(?:^|,)\s*(?:{re.escape(_HOME['state'])}|{re.escape(_HOME['state_name'])})\s*(?:,|$)(?!\s*d\.?\s?c\b)", re.I)
_DC = re.compile(r"\bwashington,?\s*d\.?\s?c\b|\bdistrict of columbia\b", re.I)
_REMOTE = _words(r"remote", r"work from home", r"wfh")


def _location_points(location, is_remote, title):
    loc = (location or "").strip()
    remote = bool(is_remote) or bool(_REMOTE.search(loc)) or bool(_REMOTE.search(title))
    in_wa = bool(_IN_HOME_STATE.search(loc)) and not _DC.search(loc)
    city = loc.split(",")[0] if loc else ""
    no_state = "," not in loc
    if (in_wa or no_state) and _HOME_CORE.search(city):
        return 15.0, HOME_LABELS[0]
    if (in_wa or no_state) and _HOME_NEAR.search(city):
        return 13.0, HOME_LABELS[1]
    if in_wa:
        return (11.0 if remote else 8.0), _HOME["state_name"]
    if remote:
        return 11.0, "Remote"
    # Big US metros outrank small towns (user priority, 2026-09-27)
    metro = metro_of(loc)
    if metro:
        return 10.0, metro
    return 5.0, ""


# --------------------------------------------------------------------------------------
# Description rules (Indeed only; LinkedIn rows have no description)
# --------------------------------------------------------------------------------------

# RTX/Collins print a "U.S. Citizen, U.S. Person, or Immigration Status Requirements:" header on
# every post, restricted or not; the header itself is not a requirement.
_US_PERSON = re.compile(r"\b(?:u\.?\s?s\.?|us)\s+persons?\b(?!,?\s+or immigration status)", re.I)
_EXPORT_REQ = re.compile(
    r"\b(?:position|role|candidates?|applicants?|job)\s+(?:must|will need to|is required to|are required to)\s+"
    r"(?:meet|comply with|satisfy)\s+(?:u\.?s\.?\s+)?export control"
    r"|\b(?:due to|because of|to comply with|in order to comply with|to conform to|pursuant to)\s+"
    r"(?:applicable\s+)?(?:u\.?s\.?\s+)?(?:government\s+)?(?:export|itar)\b", re.I)
_CLEARANCE = re.compile(
    r"\b(?:security|secret|top[- ]secret|ts\s*/\s*sci|dod|government|federal|public trust|interim|active)"
    r"\s+(?:security\s+)?clearances?\b"
    r"|(?<!customs )\bclearance (?:is |will be )?required\b(?!\s*:?\s*(?:none|no\b|n/a|not))"
    r"|\bts\s*/\s*sci\b|\btop secret\b|\bpolygraph\b(?! protection)"
    r"|\bability to obtain (?:and maintain )?(?:a |an )?(?:[a-z/]+ ){0,2}clearance\b"
    r"|\bpublic trust (?:clearance|position|background investigation|suitability|classification)\b", re.I)
_CITIZEN = re.compile(
    r"\b(?:must|required to|need to|shall|will) (?:be|possess|have|hold) (?:a |an )?"
    r"(?:u\.?\s?s\.?|united states|us) citizen(?:ship)?\b"
    r"|\b(?:u\.?\s?s\.?|united states|us) citizenship (?:is |will be )?(?:required|mandatory|a requirement)\b"
    r"|\brequires? (?:u\.?\s?s\.?|united states|us) citizenship\b"
    r"|\b(?:u\.?\s?s\.?|united states|us) citizens? only\b|\bonly (?:u\.?\s?s\.?|united states|us) citizens\b"
    r"|\b(?:u\.?\s?s\.?|united states|us) citizens?(?:hip)?,? (?:or |and )?(?:u\.?s\.? nationals?,? )?(?:or |and )?"
    r"(?:lawful )?(?:permanent residents?|green card)"
    r"|^\s*[-•]?\s*(?:must be (?:a )?)?(?:u\.?\s?s\.?|us|united states) citizen(?:ship)?\s*(?:required|only)?\s*$",
    re.I | re.M)
# "Must be a U.S. citizen, national, permanent resident, OR an alien authorized to work"
# is generic work-authorization boilerplate (UPS), not a citizenship requirement.
_GENERIC_AUTH = re.compile(r"authorized to work|authorized for employment|work authorization|"
                           r"employment authorization|eligible to work|\balien\b", re.I)
_STEM_OPT_BLOCK = re.compile(
    r"\bnot (?:be )?(?:eligible|able) to (?:extend|support|sponsor|participate in) (?:the )?stem\b"
    r"|\bdoes not (?:use|participate in) e-?verify\b"
    r"|\b(?:not|unable to|cannot|can ?not|will not|won't|do not|does not) (?:sponsor|support|accept|consider)"
    r"[^.]{0,80}\b(?:opt|cpt|f-?1|stem)\b"
    r"|\b(?:f-?1|opt|cpt)\b[^.]{0,40}\b(?:not eligible|ineligible|not accepted|not considered)", re.I)
_NO_FUTURE_SPONSOR = re.compile(
    r"\b(?:now or in the future|now or at any time in the future|current or future|future need)\b"
    r"[^.]{0,60}\bsponsorship\b"
    r"|\bsponsorship\b[^.]{0,60}\b(?:now or in the future|current or future|now or at any time in the future)\b"
    r"|\bwithout (?:the )?need (?:for|of) (?:\w+ ){0,3}sponsorship\b", re.I)
_NO_SPONSOR = re.compile(
    r"\b(?:not|unable to|cannot|can ?not|will not|won't|does not|do not|no)\s+(?:be\s+)?(?:able\s+to\s+)?"
    r"(?:currently\s+)?(?:offer|provide|support|sponsor)\w*\s+(?:\w+\s+){0,3}?"
    r"(?:visas?|sponsorship|work authorization|h-?1b)\b"
    r"|\bsponsorship (?:is )?not (?:available|offered|provided)\b"
    r"|\bnot (?:eligible|available) for (?:visa |immigration )?sponsorship\b"
    r"|\bno (?:visa |immigration )?sponsorship\b", re.I)
_SPONSOR_YES = re.compile(
    r"\b(?:visa|h-?1b|opt|immigration) sponsorships? (?:is |are )?(?:available|provided|offered)\b"
    r"|\bvisa sponsorships? \(|\bh-?1b\s*/\s*opt sponsorship\b|\bopt sponsorship\b"
    r"|\bstem opt (?:eligible|friendly|welcome)\b"
    r"|\b(?:we|will|can|happy to|able to) sponsor\b", re.I)
_SPONSOR_LIMITED = re.compile(r"\blimited (?:immigration|visa) sponsorship may be available\b", re.I)
_EVERIFY = re.compile(r"\bparticipates? in (?:the )?e-?verify\b|\be-?verify (?:employer|participant|program participant)\b"
                      r"|\buses e-?verify\b|\bis an e-?verify\b", re.I)

# Skills/domain words from her background; counted once each. Single words are matched as
# whole tokens, phrases with a word-boundary regex.
_REL_WORDS = set(_CFG["relevance_words"])
_REL_PHRASES = _words(*_CFG["relevance_phrases"])
_WORD_RX = re.compile(r"[a-z0-9][a-z0-9&\-]*")

# Requirements after these headings are "nice to have", not required.
_PREFERRED_LITERALS = ("preferred qualifications", "preferred skills", "preferred experience",
                       "preferred requirements", "desired qualifications", "desired skills",
                       "nice to have", "nice-to-have", "bonus points", "preferred:",
                       "additional qualifications", "pluses")
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15}
_NUM = r"(\d{1,2}(?:\.\d)?|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen)"
_YEARS_PATTERNS = [
    # "5+ years of program or project management experience", "3-5 years' experience",
    # "five (5) years of relevant work experience"
    re.compile(r"\b" + _NUM + r"\s*(?:\(\s*\d{1,2}\s*\)\s*)?(?:\+\s*)?(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?"
               r"(?:years?|yrs?)['’]?\s+(?:of\s+)?(?:[a-z/&,\-]+\s+){0,9}?(?:experience|exp)\b", re.I),
    # "5+ years in revenue operations", "3+ years working with suppliers"
    re.compile(r"\b(\d{1,2})\s*\+\s*(?:years?|yrs?)\s+(?:in|of|within|working|managing|leading)\b", re.I),
    # "minimum of 5 years", "at least 3 years"
    re.compile(r"\b(?:minimum(?: of)?|at least|requires?|required)\s+" + _NUM + r"\s*\+?\s*(?:years?|yrs?)\b", re.I),
    # "Minimum Management Experience - 3 years"
    re.compile(r"experience\s*[-:–]\s*" + _NUM + r"\s*\+?\s*(?:years?|yrs?)\b", re.I),
]
_YEARS_SKIP_CTX = re.compile(r"in lieu|without (?:a |an )?(?:\w+ )?degree|high school|no degree|"
                             r"equivalent combination|or equivalent|associate'?s degree|years of age|"
                             r"years old", re.I)
_DEGREE_CTX = re.compile(r"\bbachelor'?s?\b|\bmaster'?s?\b|\bmba\b|\bph\.?d\b|\bdegree\b", re.I)


def _to_years(tok):
    tok = tok.lower()
    if tok in _NUM_WORDS:
        return float(_NUM_WORDS[tok])
    try:
        return float(tok)
    except ValueError:
        return None


def _norm_desc(description):
    """Undo markdown escapes (e\\-verify, 5\\+) and bold markers; keep line breaks."""
    d = re.sub(r"\\([^\w\s])", r"\1", description or "")
    d = d.replace("*", " ").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[ \t\r\f\v]+", " ", d)


def _find_heading(dl, phrases, start=0):
    """Earliest whole-word occurrence (at or after `start`) of any phrase, else None."""
    best = None
    for p in phrases:
        i = dl.find(p, start)
        while i >= 0:
            end = i + len(p)
            if (i == 0 or not dl[i - 1].isalnum()) and (end >= len(dl) or not dl[end].isalnum()
                                                        or not p[-1].isalnum()):
                best = i if best is None else min(best, i)
                break
            i = dl.find(p, i + 1)
    return best


def _extract_years_required(description):
    """Years of experience the posting REQUIRES (0 if none stated).

    Reads only the required section (text before "Preferred qualifications"). Every basic
    qualification must be met, so plain requirements take the LARGEST stated minimum. A
    requirement tied to a degree on the same line ("Bachelor's + 6 years OR Master's + 4
    years", "Bachelor's degree and two years") is an alternative path — the candidate is
    assumed to hold the degree — so the result is min(largest plain, smallest degree path).
    Degree substitutions ("in lieu of a degree, 9 years", "or equivalent (minimum 12
    years)") are ignored.
    """
    if not description:
        return 0
    d = _norm_desc(description)
    cut = _find_heading(d.lower(), _PREFERRED_LITERALS, start=150)
    if cut is not None:
        d = d[:cut]
    plain, degree_path = [], []
    for line in d.split("\n"):
        ll = line.lower()
        if "year" not in ll and "yrs" not in ll:
            continue
        for rx in _YEARS_PATTERNS:
            for m in rx.finditer(line):
                y = _to_years(m.group(1))
                if y is None or y <= 0 or y > 20:
                    continue
                left = re.split(r"[.;•]\s", line[max(0, m.start() - 120):m.start()])[-1]
                if _YEARS_SKIP_CTX.search(left):
                    continue
                (degree_path if _DEGREE_CTX.search(left) else plain).append(y)
    best = min(max(plain) if plain else 99.0, min(degree_path) if degree_path else 99.0)
    return 0 if best == 99.0 else best


_VISA_WORD = re.compile(r"\b(?:opt|cpt|f-?1|stem)\b")


def _near(dl, rx, anchors, before=160, after=120):
    """Yield rx matches found in windows around each literal anchor occurrence.

    Pure speed optimisation: the patterns are word-boundary regexes that are slow to scan
    across a 10K-character description; every one of them contains one of its anchors, so
    only the text around the anchors needs scanning.
    """
    for a in anchors:
        i = dl.find(a)
        while i >= 0:
            yield from rx.finditer(dl, max(0, i - before), min(len(dl), i + len(a) + after))
            i = dl.find(a, i + len(a))


def _first_near(dl, rx, anchors, before=160, after=120):
    return next(_near(dl, rx, anchors, before, after), None)


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------

def _norm_title(title):
    t = (title or "").lower().replace("–", "-").replace("—", "-").replace("’", "'")
    return re.sub(r"\s+", " ", t).strip()


def _role_points(t):
    """Title -> (points 0-50, family label, cap or None, red flag or None)."""
    core = bool(_TITLE_TARGET_CORE.search(t))
    if "clinical" in _EXCLUDED and (_CLINICAL.search(t)):
        return 5.0, "clinical", 25.0, "Clinical / licensed-healthcare role"
    if "healthcare" in _EXCLUDED and (_HEALTH_CONTEXT.search(t) and not core):
        return 10.0, "healthcare delivery", 35.0, "Healthcare-delivery role, not SCM/ops"
    if "food" in _EXCLUDED and (_FOOD_FRONTLINE.search(t) or (_FOOD_CONTEXT.search(t) and not core)):
        return 5.0, "food service", 20.0, "Food service / hospitality role"
    if "retail" in _EXCLUDED and (_RETAIL_STORE.search(t)):
        return 5.0, "retail store", 22.0, "Retail-store / front-line role"
    if "driving" in _EXCLUDED and (_DRIVING.search(t)):
        return 5.0, "driving", 18.0, "Driving / equipment-operator role"
    if "warehouse" in _EXCLUDED and (_WAREHOUSE_LABOR.search(t)):
        return 5.0, "warehouse labor", 20.0, "Warehouse / hourly labor role"
    if "trades" in _EXCLUDED and (_TRADES.search(t)):
        return 5.0, "trades", 20.0, "Skilled-trade / technician role"
    if "unrelated" in _EXCLUDED and (_UNRELATED.search(t)):
        return 5.0, "unrelated", 22.0, "Unrelated field"

    best, label = 12.0, "none"
    matched = set()
    for pts, name, rx in _FAMILIES:
        if rx.search(t):
            matched.add(name)
            if pts > best:
                best, label = float(pts), name
    if len(matched & _TOP_FAMILIES) >= 2:  # "Supply Chain Risk Analyst", "Ops Program Manager"
        best = min(50.0, best + 2.0)

    if label == "risk/compliance":
        if _RISK_OFFDOMAIN.search(t) and not re.search(r"\b(?:fraud|trust (?:&|and) safety|kyc|aml)\b", t):
            best = min(best, 40.0)
        if re.search(r"\bcompliance\b", t) and not re.search(r"\b(?:risk|fraud|kyc|aml|trade)\b", t) \
                and _COMPLIANCE_OFFDOMAIN.search(t):
            best = min(best, 38.0)

    cap, flag = None, None
    head = re.split(r",|\(|:|\s[-|]\s", t, maxsplit=1)[0]
    tech = "engineering" in _EXCLUDED
    if tech and _TECH_HARD.search(head):
        best = min(best, 34.0 if _PGM_TITLE.search(t) else 18.0)
        cap, flag = 45.0, "Engineering / technical role"
    elif "it_security" in _EXCLUDED and (_TECH_SOFT_ANY.search(_TPM_PHRASE.sub(" ", t))
                                          or _TECH_SOFT_HEAD.search(head)):
        best = min(best, 34.0 if (core or _PGM_TITLE.search(t)) else 20.0)
    if cap is None and TECHNICAL_PM_CAP is not None and _TECHNICAL_PM.search(t):
        cap, flag = TECHNICAL_PM_CAP, "Technical PM role — ranked below non-technical PM / ops roles"
    other = bool(_OTHER_FUNCTION.search(t)) or (
        bool(_FINANCE_FUNCTION.search(t)) and label != "risk/compliance")
    if other:
        if label in ("risk/compliance", "supply chain", "planning", "risk-adjacent"):
            best = min(best, 36.0)
        elif label in ("operations analytics", "program management", "PM / business-analysis adjacent",
                       "generic operations", "generic operations / data"):
            best = min(best, 30.0)
        else:
            best = min(best, 16.0)
    return best, label, cap, flag


def _level_points(t):
    """Title seniority -> (points 0-20, kind)."""
    if _EXEC.search(t):
        return 0.0, "exec"
    nums = [(_ROMAN.get(n.lower()) or int(n)) for n in _LEVEL_NUM.findall(t)]
    nums += [int(n) for n in _IC_LEVEL.findall(t)]
    level = min(nums) if nums else None
    if re.search(r"\b(?:senior|sr)\.? staff\b", t):
        return 6.0, "senior manager"
    senior = bool(_SENIOR.search(t)) or bool(_STAFF_SENIOR.search(t))
    manager = bool(_MANAGER.search(t))
    pgm = bool(_PGM_TITLE.search(t))
    if senior and manager:
        return 6.0, "senior manager"
    if manager and level is not None and level >= 3:
        return (9.0 if level == 3 else 6.0), "senior manager"
    if senior or (level is not None and level >= 3):
        return (14.0 if (level is None or level <= 3) else 10.0), "senior IC"
    if manager and _ASSIST_MGR.search(t):
        return 17.0, "assistant manager"
    if manager:
        base = 17.0 if pgm else 14.0
        return (base + 2.0 if level in (1, 2) else base), "manager"
    if _LEAD.search(t):
        return 12.0, "lead"
    if _INTERN_TITLE.search(t):
        return 18.0, "intern"
    if _ENTRY.search(t) or level in (1, 2):
        return 19.0, "entry"
    if re.search(r"\b(?:analysts?|specialists?|buyers?|planners?|associates?|agents?|"
                 r"representatives?|administrators?|officers?|advisors?|investigators?|auditors?)\b", t):
        return 20.0, "individual contributor"
    if re.search(r"\b(?:coordinators?|assistants?)\b", t):
        return 17.0, "coordinator"
    if re.search(r"\bconsultants?\b", t):
        return 17.0, "consultant"
    return 15.0, "unspecified"


def _employer_points(approvals):
    """0 approvals is "not in the USCIS top-1000 list" (unknown), not "never sponsors"."""
    if approvals <= 0:
        return 3.0
    return 3.5 + 4.5 * min(1.0, math.log(max(approvals, 20) / 20.0) / math.log(4500 / 20.0))


def _freshness_points(date_posted, scraped_at):
    try:
        posted = datetime.strptime(str(date_posted)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return 1.5, None
    try:
        ref = datetime.strptime(str(scraped_at)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        ref = date.today()
    age = max(0, (ref - posted).days)
    return 4.0 * max(0.0, 1.0 - age / 14.0) - (2.0 if age > 45 else 0.0), age


def score_job_quick(title, company, location, description, job_type=None, date_posted=None,
                    scraped_at=None, is_remote=None, salary_min=None):
    """Score a single job using rules. Returns (score, reasoning, highlights, red_flags).

    Only title/company/location/description are required; the keyword arguments (all
    optional) let score_all_unscored pass posting type, date, remote flag and salary.
    """
    title = title or ""
    company = company or ""
    t = _norm_title(title)
    highlights, red_flags = [], []
    caps = []

    if not t:
        return 0.0, f"(no title) at {company}. Missing title.", [], ["Missing title"]

    # --- role & seniority (title) ---
    role, family, cap, flag = _role_points(t)
    if cap is not None:
        caps.append(cap)
        red_flags.append(flag)
    level, kind = _level_points(t)
    if kind == "exec":
        caps.append(45.0)
        red_flags.append("Director / VP / principal-level title — far too senior")
    elif kind == "senior manager":
        red_flags.append("Senior-manager level title (likely 5-8+ years)")

    # --- internship handling ---
    intern_title = bool(_INTERN_TITLE.search(t))
    bonus = 0.0     # positive signals: scaled by role relevance below
    penalty = 0.0   # negative signals: applied in full
    if intern_title and _CFG.get("internships") == "fallback":
        penalty -= 6.0
        red_flags.append("Internship — fallback only (full-time is the priority)")
    elif job_type == "internship":
        if kind in ("exec", "senior manager", "senior IC", "manager", "assistant manager", "lead"):
            caps.append(35.0)
            red_flags.append("Listed under internships, but the title is a senior/manager role — not an internship target")
        else:
            penalty -= 2.0
            red_flags.append("Listed under internships, but the title is not an internship")

    if _RETURNSHIP.search(t):
        penalty -= 15.0
        red_flags.append("Returnship — for candidates coming back from a multi-year career break")
    if _CONTRACT_TITLE.search(t):
        penalty -= 4.0
        red_flags.append("Contract / temporary / part-time (STEM OPT needs a bona fide employer)")

    # --- F-1 eligibility from title & company ---
    clearance_hit = _TITLE_CLEARANCE.search(t) if _VISA else None
    if clearance_hit and clearance_hit.group(0).startswith("defen") and _TITLE_DEFENSE_OK.search(t):
        clearance_hit = None
    if clearance_hit:
        caps.append(30.0)
        red_flags.append("Title signals clearance / defense / citizenship requirement (F-1 ineligible)")
    elif _VISA and _TITLE_GOVERNMENT.search(t):
        caps.append(40.0)
        red_flags.append("Government / federal-program role (usually U.S. citizens only)")
    if _VISA and _FEDERAL_AGENCY.search(company):
        caps.append(35.0)
        red_flags.append("U.S. federal agency — citizenship required")

    desc = _norm_desc(description) if description else ""
    dl = desc.lower()
    has_desc = len(dl) > 200
    defense_employer = _VISA and bool(_DEFENSE_EMPLOYER.search(company))
    leo = _VISA and bool(_AMAZON_LEO.search(t)) and bool(re.search(r"\bamazon\b", company, re.I))
    if (defense_employer or leo) and not has_desc:
        caps.append(40.0)
        red_flags.append("Employer/program usually requires U.S.-person status or clearance (ITAR) — verify")
    elif defense_employer or leo:
        penalty -= 5.0

    # --- description signals ---
    years_req = 0
    desc_pts = 2.0
    if has_desc and _VISA:
        if (_first_near(dl, _US_PERSON, ("person",), 12, 40)
                or _first_near(dl, _EXPORT_REQ, ("export", "itar"), 120, 20)
                or _first_near(dl, _CLEARANCE, ("clearance", "secret", "ts/sci", "ts / sci", "polygraph",
                                                "public trust"), 90, 90)):
            caps.append(30.0)
            red_flags.append("Requires security clearance or U.S.-person status (ITAR/EAR) — F-1 ineligible")
        else:
            for m in _near(dl, _CITIZEN, ("citizen",), 90, 90):
                if not _GENERIC_AUTH.search(dl, m.end(), m.end() + 300):
                    caps.append(30.0)
                    red_flags.append("U.S. citizenship required — F-1 ineligible")
                    break

        sponsor_anchors = ("sponsor", "visa", "h-1b", "h1b", "authorization")
        stem_block = (_first_near(dl, _STEM_OPT_BLOCK, ("stem", "verify"), 120, 20)
                      or any(_STEM_OPT_BLOCK.search(dl, max(0, v.start() - 130), v.end() + 60)
                             for v in _VISA_WORD.finditer(dl)))
        if stem_block:
            penalty -= 15.0
            red_flags.append("Will not support OPT/STEM OPT or no E-Verify (STEM extension impossible)")
        elif _first_near(dl, _NO_FUTURE_SPONSOR, ("sponsor",), 120, 120):
            penalty -= 8.0
            red_flags.append("No visa sponsorship now or in the future")
        elif _first_near(dl, _NO_SPONSOR, sponsor_anchors, 120, 60):
            penalty -= 6.0
            red_flags.append("States it does not sponsor visas")
        else:
            if _first_near(dl, _SPONSOR_YES, ("sponsor",), 60, 60):
                bonus += 3.0
                highlights.append("Mentions visa sponsorship")
            elif _first_near(dl, _SPONSOR_LIMITED, ("sponsor",), 30, 40):
                bonus += 1.0
            if _first_near(dl, _EVERIFY, ("verify",), 40, 30):
                bonus += 1.5
                highlights.append("E-Verify employer (STEM OPT eligible)")

    if has_desc:
        years_req = _extract_years_required(desc)
        if years_req:
            if years_req <= 2:
                bonus += 2.0
            elif years_req <= 4:
                bonus += 4.0
                highlights.append(f"Requires {years_req:g} years — matches {_NAME}'s {CANDIDATE_YEARS}")
            elif years_req <= 5:
                # One year short is routinely accepted, especially with a master's
                # ("Bachelor's + 5 or Master's + 3" is a common formula).
                bonus += 1.0
                red_flags.append(f"Requires {years_req:g}+ years — one short of {_NAME}'s {CANDIDATE_YEARS}; usually fine with a master's")
            elif years_req <= 6:
                penalty -= 6.0
                red_flags.append(f"Requires {years_req:g}+ years ({_NAME} has {CANDIDATE_YEARS})")
            elif years_req < 10:
                penalty -= 14.0
                caps.append(60.0)
                red_flags.append(f"Requires {years_req:g}+ years ({_NAME} has {CANDIDATE_YEARS})")
            else:
                penalty -= 22.0
                caps.append(45.0)
                red_flags.append(f"Requires {years_req:g}+ years ({_NAME} has {CANDIDATE_YEARS})")

        kws = {w.rstrip("s") for w in set(_WORD_RX.findall(dl)) & _REL_WORDS}
        kws |= {m.group(0).split()[0] + "+" for m in _REL_PHRASES.finditer(dl)}
        desc_pts = min(4.0, 4.0 * len(kws) / 12.0)

    # --- pay ---
    try:
        s = float(salary_min) if salary_min is not None else None
    except (TypeError, ValueError):
        s = None
    if s is not None and s > 0:
        if s < 22:
            penalty -= 5.0
            red_flags.append(f"Hourly pay from ${s:g}/hr")
        elif 15000 <= s < 45000:  # 1K-15K is usually a monthly figure
            penalty -= 4.0
            red_flags.append(f"Pay from ${s:,.0f}/yr")

    # --- location, employer, freshness ---
    loc_pts, loc_label = _location_points(location, is_remote, t)
    approvals = _h1b_approvals(company)
    emp_pts = _employer_points(approvals)
    fresh_pts, age = _freshness_points(date_posted, scraped_at)

    # Role relevance gates everything else: a borderline title at a perfect employer in
    # Seattle must not outrank a core title with average context. Positive context is
    # scaled by role/50; penalties apply in full. A core title (50) at IC level, Seattle,
    # top sponsor, posted today scores ~99 before description bonuses.
    rf = role / 50.0
    raw = role + rf * (level + loc_pts + emp_pts + fresh_pts + desc_pts + bonus) + penalty
    score = round(max(0.0, min([100.0, raw] + caps)), 1)

    # --- explanation ---
    if role >= 44:
        highlights.insert(0, f"Title matches a priority family ({family})")
    elif role >= 34:
        highlights.insert(0, f"Title partially relevant ({family})")
    elif cap is None and role <= 16:
        red_flags.append("Not relevant to the target roles")
    if loc_label:
        highlights.append(loc_label)
    if approvals >= 100:
        highlights.append(f"Strong H-1B sponsor ({approvals:,} USCIS approvals)")
    if age is not None and age > 30:
        red_flags.append(f"Posted {age} days before scrape — may be stale")

    reasoning = f"{title} at {company}. "
    if highlights:
        reasoning += " ".join(h if h.endswith(".") else h + "." for h in highlights) + " "
    if red_flags:
        reasoning += " ".join(r if r.endswith(".") else r + "." for r in red_flags)
    return score, reasoning.strip(), highlights, red_flags


def score_all_unscored():
    """Score all unscored jobs using quick rules. Every NULL-score row gets a score."""
    conn = get_db()
    results = conn.execute("""
        SELECT job_id, title, company, location, description, job_type, date_posted,
               scraped_at, is_remote, salary_min
        FROM jobs WHERE score IS NULL
    """).fetchall()
    conn.close()

    if not results:
        return 0

    # Score in memory, then write once: a connection + commit per row (the old
    # update_job_score loop) is what kept producing "database is locked".
    now = datetime.now().isoformat()
    updates = []
    for (job_id, title, company, location, description, job_type, date_posted,
         scraped_at, is_remote, salary_min) in results:
        # No `continue` on a falsy title: a row left NULL is invisible in every panel and
        # breaks the zero-unscored invariant. score_job_quick scores it 0 with a flag.
        updates.append((*score_row(title, company, location, description, job_type,
                                   date_posted, scraped_at, is_remote, salary_min), now, job_id))

    conn = get_db()
    with conn:
        conn.executemany(
            "UPDATE jobs SET score = ?, score_breakdown = ?, scored_at = ? WHERE job_id = ?",
            updates)
    conn.close()
    return len(updates)


def score_row(title, company, location, description, job_type, date_posted,
              scraped_at, is_remote, salary_min):
    """(score, breakdown_json) for one DB row — shared with scripts/rescore_all.py."""
    score, reasoning, highlights, red_flags = score_job_quick(
        title or "", company or "", location or "", description or "",
        job_type=job_type, date_posted=date_posted, scraped_at=scraped_at,
        is_remote=is_remote, salary_min=salary_min)
    return score, json.dumps({'score': score, 'reasoning': reasoning,
                              'highlights': highlights, 'red_flags': red_flags})


if __name__ == "__main__":
    from rich.console import Console
    console = Console()
    count = score_all_unscored()
    console.print(f"[green]Quick-scored {count} jobs[/green]")
