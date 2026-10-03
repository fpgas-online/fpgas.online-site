# pibfpgas - the FPGA board pages


from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from fleet.services import board_claims, found_boards, machine_hosts
from pibup.forms import UploadFileForm

from .models import Pi

# fpgas-verify's board keys (fpga-board-found "board") as people know them;
# the same titles as its own status table (fpgas.online-test-designs
# scripts/collect_verify_status.py BOARD_TITLES).
BOARD_TITLES = {
    "acorn": "Acorn",
    "arty": "Arty A7",
    "netv2": "NeTV2",
    "tt": "TT FPGA",
    "fomu": "Fomu EVT",
}


def board_title(board):
    """A found board as people read it: its title and, when one was read,
    its variant ("Acorn (cle-215+)"). A board this site has no title for
    keeps the key the Pi sent."""
    title = BOARD_TITLES.get(board["board"], board["board"])
    variant = board.get("variant")
    return f"{title} ({variant})" if variant and variant != "-" else title


def speaker(pi, newest, hosts):
    """The serial of the machine that speaks for a row: the newest registered
    with its hostname (the port it is on), or the one with its serial unless
    that machine is registered on another port; None when there is none."""
    if pi.hostname in newest:
        return newest[pi.hostname]
    if hosts.get(pi.serial_no) in ("", pi.hostname):
        return pi.serial_no
    return None


def with_boards(pis):
    """The Pis, each with `.boards`: what the machine on its port found this
    boot (found_boards), titled. There is nothing else to name a board by: a
    port whose Pi found no board this boot names none."""
    pis = list(pis)
    newest, hosts = machine_hosts()
    found = found_boards()
    for pi in pis:
        pi.found = found.get(speaker(pi, newest, hosts), [])
        pi.boards = ", ".join(board_title(board) for board in pi.found)
    return pis


def shown(pis):
    """The Pis to offer. With FPGAS_REQUIRE_VERIFIED, only those whose FPGA
    check passed this boot: the others still boot and take ssh, so someone
    can log in and see what is wrong, but users are not sent to them. A row
    is claimed by the newest Pi registered with its hostname (the port it is
    on), or by its serial unless that Pi is registered on another port."""
    if not settings.FPGAS_REQUIRE_VERIFIED:
        return list(pis)
    hosts, serials = board_claims()
    return [pi for pi in pis
            if pi.hostname in hosts
            or (pi.serial_no in serials and serials[pi.serial_no] in ("", pi.hostname))]

def home(request):

    pis = with_boards(shown(Pi.objects.all()))

    return render(request, "index.html",
            {
                'pis': pis,
                "domain_name": settings.DOMAIN_NAME,
                })


def one(request, pino, template='fpga.html'):

    # pino: Pi Number (the port on the network switch the Pi is plugged into.)
    # template: the template to render (used to hack in the tt board page.)

    pi = get_object_or_404(Pi, port=pino)
    if not shown([pi]):
        raise Http404("this board's FPGA check has not passed this boot")
    (pi,) = with_boards([pi])

    form = UploadFileForm()

    return render(request, template,
            {
                "pi": pi,
                "pino": pino,
                "pw": settings.PI_PW,
                "domain_name": settings.DOMAIN_NAME,
                "form": form,
                })


def tt(request):
    # the TT board page is port 21's: only when the Pi there found a TT board
    pi = Pi.objects.filter(port=21).first()
    if pi is None or not any(board["board"] == "tt" for board in with_boards([pi])[0].found):
        raise Http404("no TT board found on port 21 this boot")
    return one(request, 21, 'tt.html')
