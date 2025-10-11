import io
import matplotlib.pyplot as plt

def render_track_image(coords, activity_id: str) -> io.BytesIO:
    """Génère une image PNG transparente du tracé GPS, sans sauvegarde locale."""
    lats, lons = zip(*coords)
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot(lons, lats, color="#FC5200", linewidth=2)
    ax.axis("off")
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", transparent=True, pad_inches=0)
    buf.seek(0)
    plt.close(fig)
    return buf
