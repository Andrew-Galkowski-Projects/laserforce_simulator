import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { authInterceptor } from '../auth/auth.interceptor';
import { ApiError } from './api-client';
import {
  GameEvent,
  GameRound,
  GameRoundListItem,
  PlayerRoundState,
  RoundHeatmap,
  RoundMissileLog,
  TimelineEventType,
} from './rounds.models';
import { RoundsService } from './rounds.service';
import { highlight, timeline, timelineEvent } from '../matches/testing/timeline-fixtures';
import { playbackMap, roundPlayback } from '../matches/testing/playback-fixtures';

const BASE = environment.apiBaseUrl;

const ROUND_LIST_ITEM: GameRoundListItem = {
  id: 21,
  match: null,
  round_number: 1,
  team_red: 3,
  team_blue: 4,
  date_played: '2026-10-21T12:00:00Z',
  red_points: 4000,
  blue_points: 3000,
  red_team_eliminated: false,
  blue_team_eliminated: false,
  eliminated_at: 1801,
  winner: 3,
  is_completed: true,
  team_red_name: 'Alpha',
  team_blue_name: 'Bravo',
  winner_name: 'Alpha',
};

const PLAYER_STATE: PlayerRoundState = {
  id: 301,
  player: 11,
  team_color: 'red',
  role: 'commander',
  starting_lives: 15,
  starting_shots: 30,
  starting_special: 3,
  starting_missiles: 5,
  last_tagged_id: 0,
  shields: 1,
  last_downed_time: null,
  neutral_base_destroyed: false,
  opposing_base_destroyed: true,
  zone_fallback: 0,
  cell_row: null,
  cell_col: null,
  special_active_until: 0,
  is_hiding: false,
  final_lives: 7,
  final_shots: 12,
  final_special: 1,
  final_missiles: 2,
  final_medic_hits: 0,
  points_scored: 9000,
  tags_made: 40,
  shots_missed: 60,
  times_tagged: 9,
  specials_used: 2,
  own_specials_cancelled: 0,
  enemy_nuke_cancels: 1,
  ally_nuke_cancels: 0,
  medic_lives_removed_from_nuke: 0,
  lives_lost_to_nukes: 0,
  missiles_landed: 3,
  times_missiled: 0,
  resupplies_given: 0,
  combo_resupply_count: 0,
  times_tagged_in_reset_window: 1,
  follow_up_shots: 4,
  reaction_shots: 6,
  ticks_active: 1700,
  ticks_not_targetable: 50,
  ticks_reset_window: 50,
  missile_points: 1500,
  specific_tags: { '12': { tags: 3, tagged_by: 1, missiled: 2, missiled_by: 0 } },
  was_eliminated_at: 1801,
};

const SIDE = { players: [], survivors: 0, eliminated: false, elim_bonus: 0 };
const ROUND: GameRound = {
  ...ROUND_LIST_ITEM,
  player_states: [PLAYER_STATE],
  scoreboard: { red: SIDE, blue: SIDE },
};

const EVENT: GameEvent = {
  id: 9001,
  timestamp: 120,
  event_type: 'tag',
  points_awarded: 100,
  description: 'Ace tagged Bolt',
  metadata: { lives_remaining: 14 },
  actor: 11,
  target: 12,
};

describe('RoundsService', () => {
  let rounds: RoundsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    rounds = TestBed.inject(RoundsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists Rounds (slim rows) with filters and pagination', async () => {
    const result = firstValueFrom(
      rounds.list({ match: 5, arena_map: 2, ordering: 'round_number' }),
    );
    const req = http.expectOne((r) => r.url === `${BASE}/rounds/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('match')).toBe('5');
    expect(req.request.params.get('arena_map')).toBe('2');
    expect(req.request.params.get('ordering')).toBe('round_number');
    const meta = { page: 1, page_size: 20, count: 1, total_pages: 1 };
    req.flush({ data: [ROUND_LIST_ITEM], meta });
    expect(await result).toEqual({ items: [ROUND_LIST_ITEM], meta });
  });

  it('sends the single_round filter as true / false', () => {
    rounds.list({ single_round: true }).subscribe();
    rounds.list({ single_round: false }).subscribe();
    const [singles, matchRounds] = http.match((r) => r.url === `${BASE}/rounds/`);
    expect(singles.request.params.get('single_round')).toBe('true');
    expect(matchRounds.request.params.get('single_round')).toBe('false');
    const meta = { page: 1, page_size: 20, count: 0, total_pages: 1 };
    singles.flush({ data: [], meta });
    matchRounds.flush({ data: [], meta });
  });

  it('gets one Round with its player states', async () => {
    const result = firstValueFrom(rounds.get(21));
    const req = http.expectOne(`${BASE}/rounds/21/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: ROUND, meta: {} });
    const round = await result;
    expect(round).toEqual(ROUND);
    expect(round.player_states[0].points_scored).toBe(9000);
  });

  it('surfaces a cross-Account Round as ApiError 404', async () => {
    const result = firstValueFrom(rounds.get(99)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/rounds/99/`)
      .flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('creates (simulates) a standalone Round and returns the full Round', async () => {
    const body = { team_red: 3, team_blue: 4 };
    const result = firstValueFrom(rounds.create(body));
    const req = http.expectOne(`${BASE}/rounds/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    req.flush({ data: ROUND, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(ROUND);
  });

  it('downloads the Round report PDF with its server file name, no envelope unwrap', async () => {
    const result = firstValueFrom(rounds.report(21));
    const req = http.expectOne(`${BASE}/rounds/21/report/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.responseType).toBe('blob');
    const pdf = new Blob(['%PDF-1.4'], { type: 'application/pdf' });
    req.flush(pdf, {
      headers: { 'Content-Disposition': 'attachment; filename="round-21-Red-vs-Blue.pdf"' },
    });
    expect(await result).toEqual({ blob: pdf, filename: 'round-21-Red-vs-Blue.pdf' });
  });

  it("pages through a Round's events with filters", async () => {
    const result = firstValueFrom(
      rounds.events(21, {
        event_type: 'tag',
        actor: 11,
        target: undefined,
        timestamp_min: 0,
        timestamp_max: 600,
        page: 3,
        page_size: 50,
      }),
    );
    const req = http.expectOne((r) => r.url === `${BASE}/rounds/21/events/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('event_type')).toBe('tag');
    expect(req.request.params.get('actor')).toBe('11');
    expect(req.request.params.has('target')).toBe(false);
    expect(req.request.params.get('timestamp_min')).toBe('0');
    expect(req.request.params.get('timestamp_max')).toBe('600');
    expect(req.request.params.get('page')).toBe('3');
    expect(req.request.params.get('page_size')).toBe('50');
    const meta = { page: 3, page_size: 50, count: 101, total_pages: 3 };
    req.flush({ data: [EVENT], meta });
    expect(await result).toEqual({ items: [EVENT], meta });
  });

  it('reports a bad event filter as ApiError 400', async () => {
    const result = firstValueFrom(rounds.events(21, { event_type: 'movement' as never })).catch(
      (e: unknown) => e,
    );
    http
      .expectOne((r) => r.url === `${BASE}/rounds/21/events/`)
      .flush(
        {
          errors: [
            { code: 'invalid_choice', message: 'Select a valid choice.', field: 'event_type' },
          ],
          meta: {},
        },
        { status: 400, statusText: 'Bad Request' },
      );
    const err = (await result) as ApiError;
    expect(err.status).toBe(400);
    expect(err.errors[0].field).toBe('event_type');
  });

  it("gets a Round's whole timeline in one unpaged call and unwraps data", async () => {
    const body = timeline({
      events: [
        timelineEvent(),
        timelineEvent({
          tick: 3,
          event_type: 'movement',
          points: 0,
          target_id: null,
          target_name: null,
          target_side: null,
        }),
      ],
      highlights: [highlight()],
    });
    const result = firstValueFrom(rounds.timeline(21));
    const req = http.expectOne(`${BASE}/rounds/21/timeline/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ data: body, meta: {} });
    const got = await result;
    expect(got).toEqual(body);
    expect(got.events[1].target_side).toBeNull();
  });

  it('types every one of the 15 emitted event types as a TimelineEventType', () => {
    // A Record over the union fails to compile if a type is missing or extra.
    const all: Record<TimelineEventType, true> = {
      tag: true,
      locking: true,
      missiled: true,
      special: true,
      miss: true,
      resupply_ammo: true,
      resupply_lives: true,
      elimination: true,
      team_elimination: true,
      nuke_cancelled: true,
      medic_reset: true,
      movement: true,
      base_capture: true,
      combo_resupply: true,
      missile_dodge: true,
    };
    expect(Object.keys(all)).toHaveLength(15);
  });

  it("gets a Round's Playback snapshots and map in one call and unwraps data", async () => {
    const body = roundPlayback({ map: playbackMap() });
    const result = firstValueFrom(rounds.playback(21));
    const req = http.expectOne(`${BASE}/rounds/21/playback/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ data: body, meta: {} });
    const got = await result;
    expect(got).toEqual(body);
    expect(got.map?.arena_map).toEqual({ id: 7, name: 'Warehouse' });
    expect(got.players[0].snapshots[0].downed_at).toBeNull();
  });

  it('surfaces a cross-Account Playback as ApiError 404', async () => {
    const result = firstValueFrom(rounds.playback(99)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/rounds/99/playback/`)
      .flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('GETs the Missile log and returns it unwrapped', async () => {
    const body: RoundMissileLog = {
      round_id: 21,
      fired: 1,
      hit: 1,
      efficiency: 100,
      rows: [
        {
          timestamp: 125,
          timestamp_mmss: '01:02',
          actor_role: 'commander',
          target_role: 'heavy',
          result: 'hit',
          friendly_fire: false,
          description: 'Ace missiled Bob',
          points: 500,
          row_class: 'missile-row',
        },
      ],
    };
    const result = firstValueFrom(rounds.missileLog(21));
    const req = http.expectOne(`${BASE}/rounds/21/missile-log/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: body, meta: {} });
    expect(await result).toEqual(body);
  });

  it('GETs the Movement heatmap and returns it unwrapped', async () => {
    const body: RoundHeatmap = {
      round_id: 21,
      has_map: true,
      arena_map: { id: 7, name: 'Warehouse' },
      zone_size: 20,
      processed_image_url: '/maps/7/processed-image/',
      cell_occupancy_json: { '11': { '2,3': 40 } },
      player_roster: [{ id: 11, name: 'Ace', role: 'commander', team_color: 'red' }],
    };
    const result = firstValueFrom(rounds.heatmap(21));
    const req = http.expectOne(`${BASE}/rounds/21/heatmap/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: body, meta: {} });
    expect(await result).toEqual(body);
  });

  it('surfaces a cross-Account timeline as ApiError 404', async () => {
    const result = firstValueFrom(rounds.timeline(99)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/rounds/99/timeline/`)
      .flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});
