"""The figure manifest (Phase C): every dissertation figure that comes from data
has its committed file, its one generator, one script that writes it, and a
recorded state; the static checks of thesis/sync_figures.py pass."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'thesis'))
import sync_figures as sf  # noqa: E402


def test_manifest_is_complete_and_every_figure_has_one_writer():
    m = sf.load()
    assert len(m['figures']) == 62
    for fig in m['figures']:
        assert (REPO / fig['committed']).exists(), fig['committed']
        assert (REPO / fig['generator']).exists(), fig['generator']
        assert fig['status'] in sf.STATES, fig
        assert fig['status'] != 'differs' or fig.get('note'), f"{fig['thesis']}: a differing figure needs a note"
        assert sf.writers(fig) == [fig['generator']], (fig['thesis'], sf.writers(fig))


def test_static_check_passes():
    assert sf.check(thesis_images=None, strict=True) == 0
