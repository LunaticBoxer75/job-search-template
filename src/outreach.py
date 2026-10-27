"""Referral / outreach message text. Kept free of heavy imports so the dashboard can
use it without loading the scraper. The intro sentences come from config/profile.json
("identity")."""

from . import profile


def _years():
    return profile.years()


def referral_note(title, company):
    """LinkedIn connection note — must stay under 300 chars, and can't carry an attachment."""
    me = f"Hi [Name], I'm {profile.first_name()}, {profile.intro()}."
    note = (f"{me} I'm applying for the {title} role at {company} — would you be open "
            f"to referring me? Happy to send my resume. Thank you!")
    if len(note) > 300:  # long titles: drop the role name rather than overflow
        note = (f"{me} I'm applying to a role at {company} — would you be open to "
                f"referring me? Happy to send my resume. Thank you!")
    return note


def referral_message(title, company):
    """Longer version for a LinkedIn message, InMail or email, where the resume can be attached."""
    return (f"Hi [Name],\n\nI'm {profile.first_name()}, {profile.intro('long_intro')}. I'm very interested "
            f"in the {title} role at {company} and would really appreciate a referral if you think my "
            f"background could be a fit. I have attached my resume with this message.\n\nThank you!\n"
            f"{profile.first_name()}")
