import re

from teams.models import Team, Player

# RV-05: ReportLab writes every embedded RGB image as an XObject whose
# dictionary carries ``/ColorSpace /DeviceRGB`` (the one shared soft mask for
# the charts' alpha channel is ``/DeviceGray``, so it is not counted), and it
# writes each page object with its ``/XObject`` resources inline as
# ``/FormXob.<hash>`` names. Counting those byte markers needs no PDF parser.
_PDF_IMAGE_MARKER = b"/ColorSpace /DeviceRGB"
_PDF_PAGE_OBJECT = re.compile(rb"/Type /Page\s")
_PDF_XOBJECT_NAME = b"/FormXob."


def pdf_image_count(pdf: bytes) -> int:
    """The number of RGB image XObjects embedded in ``pdf``."""
    return pdf.count(_PDF_IMAGE_MARKER)


def pdf_images_per_page(pdf: bytes) -> list[int]:
    """How many image XObjects each page draws, in page order.

    Reads ReportLab's page-object resource dictionaries (``/Type /Page``
    objects are written in document order) and counts the ``/FormXob.``
    resource names in each.
    """
    counts: list[int] = []
    for match in _PDF_PAGE_OBJECT.finditer(pdf):
        start = pdf.rfind(b" 0 obj", 0, match.start())
        page_object = pdf[start : match.end()]
        counts.append(page_object.count(_PDF_XOBJECT_NAME))
    return counts


def make_team_with_slots(prefix: str) -> tuple:
    """Create a fully-slotted team with 6 players (commander/heavy/2 scouts/medic/ammo).

    Returns (team, players) where players maps role keys to Player instances.
    """
    team = Team.objects.create(name=f"{prefix} Team")
    p_cmd = Player.objects.create(team=team, name=f"{prefix} commander")
    p_hvy = Player.objects.create(team=team, name=f"{prefix} heavy")
    p_s1 = Player.objects.create(team=team, name=f"{prefix} scout1")
    p_s2 = Player.objects.create(team=team, name=f"{prefix} scout2")
    p_med = Player.objects.create(team=team, name=f"{prefix} medic")
    p_ammo = Player.objects.create(team=team, name=f"{prefix} ammo")
    team.slot_commander = p_cmd
    team.slot_heavy = p_hvy
    team.slot_scout_1 = p_s1
    team.slot_scout_2 = p_s2
    team.slot_medic = p_med
    team.slot_ammo = p_ammo
    team.save()
    players = {
        "commander": p_cmd,
        "heavy": p_hvy,
        "scout": p_s1,
        "scout_2": p_s2,
        "medic": p_med,
        "ammo": p_ammo,
    }
    return team, players
