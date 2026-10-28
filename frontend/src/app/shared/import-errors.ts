// Sorts a failed CSV import (Roster, Tournament participants / pool) into the three places a
// screen shows errors.
import { ApiError } from '../api/api-client';
import { GENERIC_ERROR_MESSAGE } from './form-errors';

/** The import form's one control; every envelope item with this `field` belongs to it. */
const FILE_FIELD = 'csv_file';

/** Where each message of a failed import goes, each list in the server's order. */
export interface ImportErrors {
  /** `csv_file` items (required, too large, not UTF-8): shown on the file control. */
  file: string[];
  /** Row items (`row:<n>` / `row:<n>:<column>`), already formatted for the list. */
  rows: string[];
  /** Everything else (`field: null`, or not an `ApiError`): the alert line. */
  alert: string[];
}

/** A row item's `field`, as `teams/api_views.py` builds it: `row:<n>` or `row:<n>:<column>`. */
const ROW_FIELD = /^row:(\d+)(?::(.+))?$/;

/**
 * "Row N (column): message", "Row N: message", or "File: message" for row 0 (the header or
 * the file as a whole); `null` when `field` is not a row field.
 */
function formatRowError(field: string, message: string): string | null {
  const match = ROW_FIELD.exec(field);
  if (!match) return null;
  const row = Number(match[1]);
  const column = match[2];
  if (row === 0) return `File: ${message}`;
  return column ? `Row ${row} (${column}): ${message}` : `Row ${row}: ${message}`;
}

/**
 * Classifies a failed roster-CSV upload (`TeamsService.importRoster`,
 * `TournamentsService.importParticipants` / `importPool`); `CsvImport.upload` calls it.
 */
export function classifyImportErrors(error: unknown): ImportErrors {
  const result: ImportErrors = { file: [], rows: [], alert: [] };
  if (!(error instanceof ApiError)) {
    result.alert.push(GENERIC_ERROR_MESSAGE);
    return result;
  }
  for (const item of error.errors) {
    const row = item.field === null ? null : formatRowError(item.field, item.message);
    if (item.field === FILE_FIELD) {
      result.file.push(item.message);
    } else if (row !== null) {
      result.rows.push(row);
    } else {
      result.alert.push(item.message);
    }
  }
  return result;
}
