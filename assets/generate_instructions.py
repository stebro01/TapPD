"""Generate instruction images for each motor test.

Run once: python assets/generate_instructions.py
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyArrowPatch
import numpy as np
from pathlib import Path

OUT = Path(__file__).parent
DPI = 150
FIGSIZE = (5, 3)
BG = "#F5F5F5"
SENSOR_COLOR = "#37474F"
HAND_COLOR = "#FFCCBC"
ARROW_COLOR = "#E65100"
TEXT_COLOR = "#212121"


def _draw_sensor(ax, cx=0.5, cy=0.08, w=0.35):
    """Draw Leap Motion sensor at bottom."""
    sensor = patches.FancyBboxPatch(
        (cx - w / 2, cy - 0.03), w, 0.06,
        boxstyle="round,pad=0.01", facecolor=SENSOR_COLOR, edgecolor="black", linewidth=1.5
    )
    ax.add_patch(sensor)
    # LED
    ax.plot(cx, cy, "o", color="#4CAF50", markersize=4, zorder=5)
    ax.text(cx, cy - 0.07, "Leap Motion", ha="center", fontsize=7, color="#757575")


def _draw_hand_outline(ax, cx, cy, spread=0.08, palm_w=0.1, facing="down"):
    """Draw simple hand shape."""
    # Palm
    palm = patches.Ellipse((cx, cy), palm_w, palm_w * 0.7, facecolor=HAND_COLOR,
                           edgecolor="#8D6E63", linewidth=1.5)
    ax.add_patch(palm)
    # Fingers
    finger_angles = [-0.4, -0.15, 0.0, 0.15, 0.35]
    finger_lens = [0.06, 0.09, 0.1, 0.09, 0.07]
    for angle, flen in zip(finger_angles, finger_lens):
        x_end = cx + spread * angle
        y_end = cy + flen if facing == "down" else cy - flen
        ax.plot([cx + spread * angle * 0.3, x_end], [cy, y_end],
                color="#8D6E63", linewidth=3, solid_capstyle="round")
    return cx, cy


def generate_finger_tapping():
    fig, ax = plt.subplots(figsize=FIGSIZE)
    fig.patch.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_aspect("equal")

    _draw_sensor(ax)

    # Side view: hand with thumb and index tapping
    # Palm (side view rectangle)
    palm = patches.FancyBboxPatch((0.3, 0.35), 0.15, 0.25,
                                  boxstyle="round,pad=0.02", facecolor=HAND_COLOR,
                                  edgecolor="#8D6E63", linewidth=1.5)
    ax.add_patch(palm)

    # Thumb - moving
    ax.plot([0.3, 0.22], [0.45, 0.35], color="#8D6E63", linewidth=4, solid_capstyle="round")
    ax.plot([0.22, 0.2], [0.35, 0.28], color="#8D6E63", linewidth=3, solid_capstyle="round")

    # Index finger - moving toward thumb
    ax.plot([0.38, 0.35], [0.6, 0.7], color="#8D6E63", linewidth=3, solid_capstyle="round")
    ax.plot([0.35, 0.28], [0.7, 0.75], color="#8D6E63", linewidth=3, solid_capstyle="round")

    # Other fingers (still)
    for dx, dy in [(0.05, 0.0), (0.08, -0.02), (0.11, -0.05)]:
        ax.plot([0.38 + dx, 0.38 + dx], [0.6, 0.68 + dy],
                color="#8D6E63", linewidth=2.5, solid_capstyle="round", alpha=0.5)

    # Double arrows showing tapping motion
    ax.annotate("", xy=(0.18, 0.22), xytext=(0.18, 0.32),
                arrowprops=dict(arrowstyle="<->", color=ARROW_COLOR, lw=2.5))
    ax.annotate("", xy=(0.26, 0.78), xytext=(0.26, 0.68),
                arrowprops=dict(arrowstyle="<->", color=ARROW_COLOR, lw=2.5))

    # Label
    ax.text(0.7, 0.75, "Daumen ↔ Zeigefinger\ntippen", ha="center", fontsize=10,
            color=TEXT_COLOR, weight="bold")
    ax.text(0.7, 0.55, "Seitenansicht\nHand planar über Sensor\nBewegung: auf/ab", ha="center",
            fontsize=8, color="#616161")
    ax.text(0.7, 0.3, "Volle Amplitude!\nSchnell & gleichmäßig",
            ha="center", fontsize=8, color=ARROW_COLOR, style="italic")

    # Distance indicator
    ax.annotate("", xy=(0.45, 0.12), xytext=(0.45, 0.35),
                arrowprops=dict(arrowstyle="<->", color="#1565C0", lw=1.5, ls="--"))
    ax.text(0.52, 0.22, "10-30 cm", fontsize=7, color="#1565C0")

    fig.tight_layout()
    fig.savefig(OUT / "instr_finger_tapping.png", dpi=DPI, facecolor=BG, bbox_inches="tight")
    plt.close(fig)


def generate_hand_open_close():
    fig, ax = plt.subplots(figsize=FIGSIZE)
    fig.patch.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_aspect("equal")

    _draw_sensor(ax)

    # Open hand (left side)
    palm1 = patches.Ellipse((0.25, 0.55), 0.12, 0.09, facecolor=HAND_COLOR,
                            edgecolor="#8D6E63", linewidth=1.5)
    ax.add_patch(palm1)
    angles = [-0.5, -0.2, 0.0, 0.2, 0.4]
    for a in angles:
        ax.plot([0.25 + a * 0.05, 0.25 + a * 0.12], [0.6, 0.72],
                color="#8D6E63", linewidth=3, solid_capstyle="round")
    ax.text(0.25, 0.42, "Offen", ha="center", fontsize=9, color="#4CAF50", weight="bold")

    # Arrow between
    ax.annotate("", xy=(0.55, 0.55), xytext=(0.42, 0.55),
                arrowprops=dict(arrowstyle="<->", color=ARROW_COLOR, lw=2.5))

    # Closed hand (right side)
    fist = patches.Ellipse((0.7, 0.55), 0.1, 0.1, facecolor=HAND_COLOR,
                           edgecolor="#8D6E63", linewidth=1.5)
    ax.add_patch(fist)
    # Curled fingers
    for a in [-0.15, -0.05, 0.05, 0.15]:
        ax.plot([0.7 + a, 0.7 + a * 0.5], [0.6, 0.63],
                color="#8D6E63", linewidth=3, solid_capstyle="round")
    ax.text(0.7, 0.42, "Geschlossen", ha="center", fontsize=9, color="#F44336", weight="bold")

    ax.text(0.5, 0.85, "Hand Öffnen/Schließen", ha="center", fontsize=11,
            color=TEXT_COLOR, weight="bold")
    ax.text(0.5, 0.28, "Handfläche nach unten\nFinger weit spreizen → Faust",
            ha="center", fontsize=8, color="#616161")

    fig.tight_layout()
    fig.savefig(OUT / "instr_hand_open_close.png", dpi=DPI, facecolor=BG, bbox_inches="tight")
    plt.close(fig)


def generate_pronation_supination():
    fig, ax = plt.subplots(figsize=FIGSIZE)
    fig.patch.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_aspect("equal")

    _draw_sensor(ax)

    # Pronation (palm down) - left
    palm1 = patches.Ellipse((0.22, 0.55), 0.14, 0.06, facecolor=HAND_COLOR,
                            edgecolor="#8D6E63", linewidth=1.5)
    ax.add_patch(palm1)
    ax.text(0.22, 0.44, "Pronation\n(Handfläche ↓)", ha="center", fontsize=7, color="#616161")

    # Rotation arrow
    arc = patches.Arc((0.48, 0.55), 0.2, 0.25, angle=0, theta1=-60, theta2=60,
                      color=ARROW_COLOR, linewidth=2.5)
    ax.add_patch(arc)
    ax.annotate("", xy=(0.56, 0.65), xytext=(0.54, 0.67),
                arrowprops=dict(arrowstyle="->", color=ARROW_COLOR, lw=2))
    ax.annotate("", xy=(0.56, 0.45), xytext=(0.54, 0.43),
                arrowprops=dict(arrowstyle="->", color=ARROW_COLOR, lw=2))

    # Supination (palm up) - right
    palm2 = patches.Ellipse((0.74, 0.55), 0.14, 0.06, facecolor="#FFE0B2",
                            edgecolor="#8D6E63", linewidth=1.5)
    ax.add_patch(palm2)
    ax.text(0.74, 0.44, "Supination\n(Handfläche ↑)", ha="center", fontsize=7, color="#616161")

    ax.text(0.5, 0.85, "Pronation / Supination", ha="center", fontsize=11,
            color=TEXT_COLOR, weight="bold")
    ax.text(0.5, 0.22, "Unterarm drehen: Handfläche auf ↔ ab\n'Glühbirne einschrauben'",
            ha="center", fontsize=8, color="#616161")

    fig.tight_layout()
    fig.savefig(OUT / "instr_pronation_supination.png", dpi=DPI, facecolor=BG, bbox_inches="tight")
    plt.close(fig)


def generate_postural_tremor():
    fig, ax = plt.subplots(figsize=FIGSIZE)
    fig.patch.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_aspect("equal")

    _draw_sensor(ax, cx=0.5, w=0.4)

    # Two hands over sensor
    for cx, label in [(0.32, "Links"), (0.68, "Rechts")]:
        palm = patches.Ellipse((cx, 0.5), 0.12, 0.08, facecolor=HAND_COLOR,
                               edgecolor="#8D6E63", linewidth=1.5)
        ax.add_patch(palm)
        # Fingers
        for a in [-0.4, -0.15, 0.0, 0.15, 0.35]:
            ax.plot([cx + a * 0.05, cx + a * 0.1], [0.55, 0.64],
                    color="#8D6E63", linewidth=2.5, solid_capstyle="round")
        ax.text(cx, 0.38, label, ha="center", fontsize=8, color="#616161")

        # Tremor squiggles
        t = np.linspace(0, 2 * np.pi, 30)
        squig_x = cx + 0.01 * np.sin(5 * t)
        squig_y = 0.5 + 0.03 * np.cos(5 * t)
        ax.plot(squig_x, squig_y, color=ARROW_COLOR, linewidth=1, alpha=0.6)

    ax.text(0.5, 0.85, "Posturaler Tremor – beide Hände", ha="center", fontsize=11,
            color=TEXT_COLOR, weight="bold")
    ax.text(0.5, 0.22, "Hände vorgestreckt, Finger gespreizt\nSo still wie möglich halten",
            ha="center", fontsize=8, color="#616161")
    ax.text(0.5, 0.75, "Getrennte Analyse: L + R", ha="center", fontsize=8,
            color="#1565C0", style="italic")

    fig.tight_layout()
    fig.savefig(OUT / "instr_postural_tremor.png", dpi=DPI, facecolor=BG, bbox_inches="tight")
    plt.close(fig)


def generate_rest_tremor():
    fig, ax = plt.subplots(figsize=FIGSIZE)
    fig.patch.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_aspect("equal")

    _draw_sensor(ax, cx=0.5, w=0.4)

    # Two relaxed hands, lower position
    for cx, label in [(0.32, "Links"), (0.68, "Rechts")]:
        palm = patches.Ellipse((cx, 0.42), 0.12, 0.08, facecolor=HAND_COLOR,
                               edgecolor="#8D6E63", linewidth=1.5)
        ax.add_patch(palm)
        # Slightly curled fingers (relaxed)
        for i, a in enumerate([-0.3, -0.1, 0.05, 0.2, 0.35]):
            curl = 0.04 + 0.01 * i
            ax.plot([cx + a * 0.04, cx + a * 0.08], [0.46, 0.46 + curl],
                    color="#8D6E63", linewidth=2.5, solid_capstyle="round")
        ax.text(cx, 0.3, label, ha="center", fontsize=8, color="#616161")

        # Subtle tremor indicators
        if cx > 0.5:  # right = affected
            t = np.linspace(0, 2 * np.pi, 20)
            ax.plot(cx + 0.015 * np.sin(4 * t), 0.42 + 0.02 * np.cos(4 * t),
                    color=ARROW_COLOR, linewidth=1.5, alpha=0.7)

    ax.text(0.5, 0.85, "Ruhetremor – beide Hände", ha="center", fontsize=11,
            color=TEXT_COLOR, weight="bold")
    ax.text(0.5, 0.68, "Hände entspannt, nicht anspannen\nTremor nicht unterdrücken",
            ha="center", fontsize=8, color="#616161")
    ax.text(0.5, 0.58, "Getrennte Analyse: L + R + Asymmetrie", ha="center", fontsize=8,
            color="#1565C0", style="italic")

    # Thigh/surface indication
    ax.plot([0.1, 0.9], [0.2, 0.2], color="#BDBDBD", linewidth=2, linestyle="--")
    ax.text(0.5, 0.16, "(Oberschenkel/Unterlage)", ha="center", fontsize=7, color="#9E9E9E")

    fig.tight_layout()
    fig.savefig(OUT / "instr_rest_tremor.png", dpi=DPI, facecolor=BG, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    generate_finger_tapping()
    generate_hand_open_close()
    generate_pronation_supination()
    generate_postural_tremor()
    generate_rest_tremor()
    print(f"Generated instruction images in {OUT}")
