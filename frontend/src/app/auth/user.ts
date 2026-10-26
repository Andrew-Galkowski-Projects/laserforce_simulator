import { Player } from '../api/players.models';

/** The Account, as `GET accounts/me/` (`MeSerializer`) sends it. */
export interface User {
  id: number;
  email: string;
  /** ISO 8601 datetime. */
  date_joined: string;
  is_staff: boolean;
  /** The linked Self-insert Player, or null. */
  self_insert: Player | null;
}

/** `RegisterForm` fields: an invite code is required. */
export interface RegisterInput {
  email: string;
  password1: string;
  password2: string;
  invite: string;
}

/** `StyledPasswordChangeForm` fields. */
export interface PasswordChangeInput {
  old_password: string;
  new_password1: string;
  new_password2: string;
}
