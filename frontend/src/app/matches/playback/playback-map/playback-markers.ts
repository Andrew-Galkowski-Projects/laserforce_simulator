// How a player's role-icon marker looks on the Playback map (RV-06): pure, so the canvas
// painting in playback-map.ts only draws what these return.
import { Side } from '../../../api/rounds.models';

/** Opacity factor for a deactivated player's marker and trail. */
export const MARKER_DIM = 0.35;

/** The marker's square side in image px: 1.5 times the old ring marker's `max(18, zone * 1.3)`. */
export function markerSize(zone: number): number {
  return 1.5 * Math.max(18, zone * 1.3);
}

/** The marker's tint and opacity: the Side colour while active, dimmed grey when deactivated. */
export function markerLook(
  side: Side,
  off: boolean,
  colours: { side: Readonly<Record<Side, string>>; muted: string },
): { colour: string; alpha: number } {
  return off
    ? { colour: colours.muted, alpha: MARKER_DIM }
    : { colour: colours.side[side], alpha: 1 };
}
