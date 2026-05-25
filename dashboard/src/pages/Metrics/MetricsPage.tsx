import { useMemo, type JSX } from 'react';

import { Link, useSearch } from '@tanstack/react-router';

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

import { cn } from '@/lib/utils';

type PeriodOption = {
  label: string;
  days: number;
};

const PERIOD_OPTIONS: PeriodOption[] = [
  { label: '7 days', days: 7 },
  { label: '14 days', days: 14 },
  { label: '30 days', days: 30 },
];

type CoverageMetric = {
  label: string;
  current: number;
  previous: number;
};

type BuildIncident = {
  origin: string;
  existingIssues: number;
  newIssues: number;
  totalIncidents: number;
  topIssues: { id: string; version: number; comment: string; count: number }[];
};

type LabData = {
  name: string;
  builds: number;
  boots: number;
  tests: number;
  prevTests: number;
  isNew: boolean;
  isExtinct: boolean;
};

const FAKE_COVERAGE: CoverageMetric[] = [
  { label: 'Trees', current: 105, previous: 100 },
  { label: 'Checkouts', current: 1000, previous: 1000 },
  { label: 'Builds', current: 11000, previous: 10000 },
  { label: 'Tests', current: 1000000, previous: 1500000 },
];

const FAKE_REGRESSIONS: BuildIncident[] = [
  {
    origin: 'maestro',
    existingIssues: 1,
    newIssues: 1,
    totalIncidents: 70,
    topIssues: [
      {
        id: 'issue-abc123',
        version: 1,
        comment: 'arm64: allmodconfig build failure in drivers/gpu',
        count: 50,
      },
      {
        id: 'issue-def456',
        version: 1,
        comment: 'riscv: defconfig warning treated as error',
        count: 20,
      },
    ],
  },
  {
    origin: 'redhat',
    existingIssues: 1,
    newIssues: 0,
    totalIncidents: 5,
    topIssues: [
      {
        id: 'issue-ghi789',
        version: 1,
        comment: 'x86_64: link error in net/ipv4',
        count: 5,
      },
    ],
  },
];

const FAKE_LABS: LabData[] = [
  {
    name: 'lava-collabora',
    builds: 0,
    boots: 50000,
    tests: 450000,
    prevTests: 700000,
    isNew: false,
    isExtinct: false,
  },
  {
    name: 'lava-broonie',
    builds: 0,
    boots: 25000,
    tests: 475000,
    prevTests: 650000,
    isNew: false,
    isExtinct: false,
  },
  {
    name: 'lab-baylibre',
    builds: 12,
    boots: 8000,
    tests: 32000,
    prevTests: 0,
    isNew: true,
    isExtinct: false,
  },
];

function formatNumber(n: number): string {
  return n.toLocaleString();
}

const PERCENTAGE_BASE = 100;
const DEFAULT_INTERVAL_DAYS = 7;

function formatDelta(current: number, previous: number): string {
  const diff = current - previous;
  if (diff === 0) {
    return 'unchanged';
  }
  const pct =
    previous !== 0 ? Math.round((diff / previous) * PERCENTAGE_BASE) : 0;
  const sign = diff > 0 ? '+' : '';
  const pctStr = previous !== 0 ? ` (${sign}${pct}%)` : '';
  return `${sign}${formatNumber(diff)}${pctStr}`;
}

function deltaColor(
  current: number,
  previous: number,
  moreIsGood = true,
): string {
  const diff = current - previous;
  if (diff === 0) {
    return 'text-gray-500';
  }
  if (moreIsGood) {
    return diff > 0 ? 'text-green-700' : 'text-red-700';
  }
  return diff > 0 ? 'text-red-700' : 'text-green-700';
}

function PeriodSelector({
  activeDays,
  onChange,
}: {
  activeDays: number;
  onChange: (days: number) => void;
}): JSX.Element {
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm font-medium text-gray-600">Period:</span>
      {PERIOD_OPTIONS.map(opt => (
        <button
          key={opt.days}
          onClick={() => onChange(opt.days)}
          className={cn(
            'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
            opt.days === activeDays
              ? 'bg-blue-600 text-white'
              : 'bg-gray-100 text-gray-700 hover:bg-gray-200',
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function CoverageSection({
  metrics,
}: {
  metrics: CoverageMetric[];
}): JSX.Element {
  return (
    <section>
      <h2 className="mb-4 text-lg font-semibold text-gray-900">Coverage</h2>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {metrics.map(metric => (
          <Link
            key={metric.label}
            to="/tree"
            className="rounded-lg border border-gray-200 bg-white p-4 transition-shadow hover:shadow-md"
          >
            <div className="text-sm font-medium text-gray-500">
              {metric.label}
            </div>
            <div className="mt-1 text-2xl font-bold text-gray-900">
              {formatNumber(metric.current)}
            </div>
            <div
              className={cn(
                'mt-1 text-sm',
                deltaColor(metric.current, metric.previous),
              )}
            >
              {formatDelta(metric.current, metric.previous)}
            </div>
          </Link>
        ))}
      </div>
    </section>
  );
}

function RegressionsSection({
  regressions,
}: {
  regressions: BuildIncident[];
}): JSX.Element {
  const totalIncidents = regressions.reduce(
    (sum, r) => sum + r.totalIncidents,
    0,
  );
  const totalExisting = regressions.reduce(
    (sum, r) => sum + r.existingIssues,
    0,
  );
  const totalNew = regressions.reduce((sum, r) => sum + r.newIssues, 0);

  return (
    <section>
      <h2 className="mb-4 text-lg font-semibold text-gray-900">
        Build Regressions
      </h2>
      {regressions.length === 0 ? (
        <p className="text-sm text-gray-500">
          No build regressions in this period.
        </p>
      ) : (
        <div className="rounded-lg border border-gray-200 bg-white">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="font-bold text-black">Origin</TableHead>
                <TableHead className="font-bold text-black">
                  Issues (known + new)
                </TableHead>
                <TableHead className="font-bold text-black">
                  Affected Builds
                </TableHead>
                <TableHead className="font-bold text-black">
                  Top Issues
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {regressions.map(row => (
                <TableRow key={row.origin} className="cursor-pointer">
                  <TableCell className="font-medium">{row.origin}</TableCell>
                  <TableCell>
                    {row.existingIssues} + {row.newIssues} ={' '}
                    {row.existingIssues + row.newIssues}
                    {row.newIssues > 0 && (
                      <span className="ml-2 rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-700">
                        {row.newIssues} new
                      </span>
                    )}
                  </TableCell>
                  <TableCell>{formatNumber(row.totalIncidents)}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-2">
                      {row.topIssues.map((issue, idx) => (
                        <Link
                          key={issue.id}
                          to="/issue/$issueId"
                          params={{ issueId: issue.id }}
                          search={s => ({
                            origin: s.origin,
                            issueVersion: issue.version,
                          })}
                          className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700 transition-colors hover:bg-blue-100 hover:text-blue-700"
                          onClick={e => e.stopPropagation()}
                        >
                          <span className="font-bold">#{idx + 1}</span>
                          <span>{formatNumber(issue.count)}</span>
                        </Link>
                      ))}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              <TableRow className="bg-gray-50 font-medium">
                <TableCell className="font-bold">Total</TableCell>
                <TableCell>
                  {totalExisting} + {totalNew} = {totalExisting + totalNew}
                </TableCell>
                <TableCell>{formatNumber(totalIncidents)}</TableCell>
                <TableCell />
              </TableRow>
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}

function TopRegressionsSection({
  regressions,
}: {
  regressions: BuildIncident[];
}): JSX.Element {
  const hasIssues = regressions.some(r => r.topIssues.length > 0);

  if (!hasIssues) {
    return (
      <section>
        <h2 className="mb-4 text-lg font-semibold text-gray-900">
          Top Regressions
        </h2>
        <p className="text-sm text-gray-500">
          No regression details in this period.
        </p>
      </section>
    );
  }

  return (
    <section>
      <h2 className="mb-4 text-lg font-semibold text-gray-900">
        Top Regressions
      </h2>
      <div className="space-y-4">
        {regressions.map(row => (
          <div
            key={row.origin}
            className="rounded-lg border border-gray-200 bg-white p-4"
          >
            <h3 className="mb-3 text-sm font-bold text-gray-700 uppercase">
              {row.origin}
            </h3>
            <div className="space-y-2">
              {row.topIssues.map((issue, idx) => (
                <Link
                  key={issue.id}
                  to="/issue/$issueId"
                  params={{ issueId: issue.id }}
                  search={s => ({
                    origin: s.origin,
                    issueVersion: issue.version,
                  })}
                  className="flex items-start gap-3 rounded-md px-3 py-2 transition-colors hover:bg-gray-50"
                >
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-gray-200 text-xs font-bold text-gray-600">
                    {idx + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <span className="text-sm text-gray-900">
                      {issue.comment}
                    </span>
                    <span className="ml-2 text-xs text-gray-500">
                      {formatNumber(issue.count)} incidents
                    </span>
                  </div>
                  <span className="shrink-0 text-xs text-blue-600">View →</span>
                </Link>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function LabsSection({ labs }: { labs: LabData[] }): JSX.Element {
  const activeLabs = labs.filter(l => !l.isExtinct);
  const extinctLabs = labs.filter(l => l.isExtinct);
  const totalTests = activeLabs.reduce((sum, l) => sum + l.tests, 0);
  const totalPrevTests = activeLabs.reduce((sum, l) => sum + l.prevTests, 0);
  const totalBoots = activeLabs.reduce((sum, l) => sum + l.boots, 0);

  return (
    <section>
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="text-lg font-semibold text-gray-900">
          Test Labs Activity
        </h2>
        <span className="text-sm text-gray-500">
          {activeLabs.length} lab{activeLabs.length !== 1 ? 's' : ''} reported
          results
        </span>
      </div>
      <div className="rounded-lg border border-gray-200 bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="font-bold text-black">Lab</TableHead>
              <TableHead className="font-bold text-black">Builds</TableHead>
              <TableHead className="font-bold text-black">Boots</TableHead>
              <TableHead className="font-bold text-black">Tests</TableHead>
              <TableHead className="font-bold text-black">
                Change (tests)
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {activeLabs.map(lab => (
              <TableRow key={lab.name}>
                <TableCell className="font-medium">{lab.name}</TableCell>
                <TableCell>{formatNumber(lab.builds)}</TableCell>
                <TableCell>{formatNumber(lab.boots)}</TableCell>
                <TableCell>{formatNumber(lab.tests)}</TableCell>
                <TableCell
                  className={cn(
                    'text-sm',
                    deltaColor(lab.tests, lab.prevTests),
                  )}
                >
                  {formatDelta(lab.tests, lab.prevTests)}
                </TableCell>
              </TableRow>
            ))}
            {extinctLabs.map(lab => (
              <TableRow key={lab.name} className="opacity-50">
                <TableCell className="font-medium text-gray-400">
                  {lab.name}
                  <span className="ml-2 text-xs text-gray-400">(inactive)</span>
                </TableCell>
                <TableCell className="text-gray-400">0</TableCell>
                <TableCell className="text-gray-400">0</TableCell>
                <TableCell className="text-gray-400">0</TableCell>
                <TableCell className="text-sm text-red-700">
                  {formatDelta(0, lab.prevTests)}
                </TableCell>
              </TableRow>
            ))}
            <TableRow className="bg-gray-50 font-medium">
              <TableCell className="font-bold">Total</TableCell>
              <TableCell>
                {formatNumber(activeLabs.reduce((sum, l) => sum + l.builds, 0))}
              </TableCell>
              <TableCell>{formatNumber(totalBoots)}</TableCell>
              <TableCell>{formatNumber(totalTests)}</TableCell>
              <TableCell
                className={cn(
                  'text-sm font-medium',
                  deltaColor(totalTests, totalPrevTests),
                )}
              >
                {formatDelta(totalTests, totalPrevTests)}
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

export const MetricsPage = (): JSX.Element => {
  const { intervalInDays } = useSearch({ from: '/_main/metrics' });

  const activeDays = useMemo(
    () => intervalInDays ?? DEFAULT_INTERVAL_DAYS,
    [intervalInDays],
  );

  return (
    <div className="flex flex-col gap-8 pb-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">
          KernelCI Metrics Summary
        </h1>
        <PeriodSelector
          activeDays={activeDays}
          onChange={() => {
            // will navigate with updated search params when wired to real data
          }}
        />
      </div>

      <CoverageSection metrics={FAKE_COVERAGE} />
      <RegressionsSection regressions={FAKE_REGRESSIONS} />
      <TopRegressionsSection regressions={FAKE_REGRESSIONS} />
      <LabsSection labs={FAKE_LABS} />
    </div>
  );
};
