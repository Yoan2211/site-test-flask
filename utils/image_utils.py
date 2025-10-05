import io
import os
import matplotlib.pyplot as plt

def render_track_image(coords, activity_id: str) -> io.BytesIO:
    """Génère une image PNG transparente du tracé GPS"""
    lats, lons = zip(*coords)
    fig, ax = plt.subplots(figsize=(6, 6))

    ax.plot(lons, lats, color="#FC5200", linewidth=2)
    ax.axis("off")
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", transparent=True, pad_inches=0)
    buf.seek(0)

    # Optionnel : sauvegarde locale
    output_dir = "static/imported_images"
    os.makedirs(output_dir, exist_ok=True)
    fig.savefig(os.path.join(output_dir, f"track_{activity_id}.png"), format="png", bbox_inches="tight", transparent=True, pad_inches=0)

    plt.close(fig)
    return buf
