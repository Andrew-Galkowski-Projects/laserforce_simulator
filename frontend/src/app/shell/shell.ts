import { BreakpointObserver } from '@angular/cdk/layout';
import { Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButton, MatIconButton } from '@angular/material/button';
import { MatListItem, MatNavList } from '@angular/material/list';
import { MatSidenav, MatSidenavContainer, MatSidenavContent } from '@angular/material/sidenav';
import { MatToolbar } from '@angular/material/toolbar';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { map } from 'rxjs';
import { APP_TITLE } from '../app-title';
import { AuthService } from '../auth/auth.service';

/** At this width and wider the sidebar sits beside the page; below it is a drawer (ADR-0043). */
const WIDE = '(min-width: 768px)';

/** ADR-0043 layout B: the signed-in frame (header + sidebar) around every page but `/login`. */
@Component({
  imports: [
    MatButton,
    MatIconButton,
    MatListItem,
    MatNavList,
    MatSidenav,
    MatSidenavContainer,
    MatSidenavContent,
    MatToolbar,
    RouterLink,
    RouterLinkActive,
    RouterOutlet,
  ],
  selector: 'app-shell',
  styleUrl: './shell.scss',
  templateUrl: './shell.html',
})
export class Shell {
  private readonly auth = inject(AuthService);

  protected readonly title = APP_TITLE;
  protected readonly user = this.auth.currentUser;
  protected readonly wide = toSignal(
    inject(BreakpointObserver)
      .observe(WIDE)
      .pipe(map((state) => state.matches)),
    { initialValue: false },
  );

  protected signOut(): void {
    // AuthService.logout() forgets the session and navigates to /login itself.
    this.auth.logout().subscribe();
  }
}
