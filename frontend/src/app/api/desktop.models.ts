/** DESK-04: a newer Desktop build of the running build's channel (`accounts/desktop-update/`). */
export interface DesktopUpdate {
  /** The newer build's label: `v0.2.0` (a Release) or `latest abc1234` (the Latest desktop build). */
  version: string;
  /** Its GitHub release page. */
  url: string;
}
