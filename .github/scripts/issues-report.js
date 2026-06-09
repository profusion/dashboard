#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { parseArgs } = require("util");
const { Octokit } = require("@octokit/rest");

const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_SEARCH_RESULTS = 1000;

function usage() {
  return [
    "Usage:",
    "  node issues-report.js --since YYYY-MM-DD [--repo owner/name] [--out path]",
    "",
    "Options:",
    "  --since   Required. Include issues created on/after this date",
    "  --repo    Optional. Default: kernelci/dashboard",
    "  --out     Optional. Output markdown path (default: repo root issues-report.md)",
    "  --help    Show this help",
  ].join("\n");
}

function parseCliArgs() {
  const { values } = parseArgs({
    options: {
      since: {
        type: "string",
      },
      repo: {
        type: "string",
        default: "kernelci/dashboard",
      },
      out: {
        type: "string",
      },
      help: {
        type: "boolean",
        default: false,
      },
    },
    allowPositionals: false,
  });

  if (values.help) {
    console.log(usage());
    process.exit(0);
  }

  if (!values.since) {
    throw new Error("Missing required --since argument\n\n" + usage());
  }

  const dateMatch = /^\d{4}-\d{2}-\d{2}$/.test(values.since);
  const parsed = new Date(`${values.since}T00:00:00Z`);
  if (!dateMatch || Number.isNaN(parsed.getTime())) {
    throw new Error("Invalid --since value. Expected YYYY-MM-DD");
  }

  const repoMatch = /^([^/\s]+)\/([^/\s]+)$/.exec(values.repo);
  if (!repoMatch) {
    throw new Error("Invalid --repo value. Expected owner/name");
  }

  const defaultOut = path.resolve(__dirname, "..", "..", "issues-report.md");
  const outputPath = values.out ? path.resolve(process.cwd(), values.out) : defaultOut;

  return {
    since: values.since,
    sinceDate: parsed,
    owner: repoMatch[1],
    repo: repoMatch[2],
    fullRepo: values.repo,
    outputPath,
  };
}

function formatDuration(ms) {
  const safeMs = Math.max(0, ms);
  const days = Math.floor(safeMs / DAY_MS);
  const hours = Math.floor((safeMs % DAY_MS) / (60 * 60 * 1000));
  const mins = Math.floor((safeMs % (60 * 60 * 1000)) / (60 * 1000));

  if (days > 0) {
    return `${days}d ${hours}h`;
  }

  if (hours > 0) {
    return `${hours}h ${mins}m`;
  }

  return `${mins}m`;
}

function formatAverageDuration(ms) {
  if (ms === null) {
    return "—";
  }

  if (ms >= DAY_MS) {
    return `${(ms / DAY_MS).toFixed(1)} days`;
  }

  return formatDuration(ms);
}

function formatDate(isoDate) {
  return new Date(isoDate).toISOString().slice(0, 10);
}

function formatPeriod(startDate, endDate) {
  const startOptions =
    startDate.getUTCFullYear() === endDate.getUTCFullYear()
      ? { month: "long", day: "numeric", timeZone: "UTC" }
      : { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" };

  const endOptions = {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  };

  const start = new Intl.DateTimeFormat("en-US", startOptions).format(startDate);
  const end = new Intl.DateTimeFormat("en-US", endOptions).format(endDate);

  return `${start} – ${end}`;
}

async function fetchIssues({ octokit, fullRepo, owner, repo, since }) {
  const query = `repo:${fullRepo} is:issue created:>=${since}`;
  const perPage = 100;

  const firstPage = await octokit.search.issuesAndPullRequests({
    q: query,
    per_page: perPage,
    page: 1,
    sort: "created",
    order: "desc",
  });

  const totalCount = firstPage.data.total_count ?? 0;
  const cappedTotal = Math.min(totalCount, MAX_SEARCH_RESULTS);
  const issues = [...firstPage.data.items];

  let page = 2;
  while (issues.length < cappedTotal) {
    const response = await octokit.search.issuesAndPullRequests({
      q: query,
      per_page: perPage,
      page,
      sort: "created",
      order: "desc",
    });

    if (response.data.items.length === 0) {
      break;
    }

    issues.push(...response.data.items);
    page += 1;
  }

  const filteredIssues = issues
    .filter((item) => !item.pull_request)
    .filter((item) => item.repository_url.endsWith(`/${owner}/${repo}`))
    .slice(0, cappedTotal);

  return {
    query,
    totalCount,
    cappedTotal,
    issues: filteredIssues,
  };
}

function aggregateIssues(issues) {
  const byAuthor = new Map();
  const closedEntries = [];

  for (const issue of issues) {
    const author = issue.user?.login || "unknown";
    if (!byAuthor.has(author)) {
      byAuthor.set(author, { open: [], closed: [] });
    }

    const base = {
      number: issue.number,
      title: issue.title,
      created: formatDate(issue.created_at),
      url: issue.html_url,
      author,
    };

    if (issue.state === "closed" && issue.closed_at) {
      const ttlMs = new Date(issue.closed_at).getTime() - new Date(issue.created_at).getTime();
      const closed = {
        ...base,
        closed: formatDate(issue.closed_at),
        timeToCloseMs: ttlMs,
        timeToClose: formatDuration(ttlMs),
      };
      byAuthor.get(author).closed.push(closed);
      closedEntries.push(closed);
    } else {
      byAuthor.get(author).open.push(base);
    }
  }

  for (const group of byAuthor.values()) {
    group.open.sort((a, b) => b.created.localeCompare(a.created) || b.number - a.number);
    group.closed.sort((a, b) => b.timeToCloseMs - a.timeToCloseMs || b.number - a.number);
  }

  const authors = [...byAuthor.keys()].sort((a, b) => {
    const aTotal = byAuthor.get(a).open.length + byAuthor.get(a).closed.length;
    const bTotal = byAuthor.get(b).open.length + byAuthor.get(b).closed.length;
    if (bTotal !== aTotal) {
      return bTotal - aTotal;
    }
    return a.localeCompare(b);
  });

  const totalOpen = authors.reduce((sum, author) => sum + byAuthor.get(author).open.length, 0);
  const totalClosed = authors.reduce((sum, author) => sum + byAuthor.get(author).closed.length, 0);

  const overallAvgMs = closedEntries.length
    ? closedEntries.reduce((sum, issue) => sum + issue.timeToCloseMs, 0) / closedEntries.length
    : null;

  return {
    byAuthor,
    authors,
    totalOpen,
    totalClosed,
    overallAvgMs,
  };
}

function renderReport({ sinceDate, today, fullRepo, query, totalCount, truncated, aggregate }) {
  const period = formatPeriod(sinceDate, today);
  const total = aggregate.totalOpen + aggregate.totalClosed;
  const openPct = total === 0 ? 0 : Math.round((aggregate.totalOpen / total) * 100);
  const closedPct = total === 0 ? 0 : Math.round((aggregate.totalClosed / total) * 100);

  const lines = [];
  lines.push(`# Issues Report — ${fullRepo} (Since ${sinceDate.toISOString().slice(0, 10)})`);
  lines.push("");
  lines.push(`**Period:** ${period}  `);
  lines.push(`**Source:** GitHub Search API (\`${query}\`)  `);
  lines.push(`**Total issues created:** ${totalCount}`);
  if (truncated) {
    lines.push("**Warning:** Search results exceed 1000 items; report is truncated.");
  }
  lines.push("");

  if (total === 0) {
    lines.push("---");
    lines.push("");
    lines.push("No issues found for this period.");
    lines.push("");
    return lines.join("\n");
  }

  lines.push("---");
  lines.push("");
  lines.push("## Summary by Author");
  lines.push("");
  lines.push("| Author | Open | Closed | Total | Avg time-to-close |");
  lines.push("|--------|------|--------|-------|-------------------|");
  for (const author of aggregate.authors) {
    const group = aggregate.byAuthor.get(author);
    const totalByAuthor = group.open.length + group.closed.length;
    const avgMs =
      group.closed.length > 0
        ? group.closed.reduce((sum, issue) => sum + issue.timeToCloseMs, 0) / group.closed.length
        : null;
    lines.push(
      `| [${author}](https://github.com/${author}) | ${group.open.length} | ${group.closed.length} | ${totalByAuthor} | ${formatAverageDuration(avgMs)} |`,
    );
  }
  lines.push("");
  lines.push(
    `**Overall:** ${aggregate.totalOpen} open (${openPct}%), ${aggregate.totalClosed} closed (${closedPct}%)`,
  );
  lines.push("");

  for (const author of aggregate.authors) {
    const group = aggregate.byAuthor.get(author);
    const totalByAuthor = group.open.length + group.closed.length;

    lines.push("---");
    lines.push("");
    lines.push(`## ${author} (${totalByAuthor} issues)`);
    lines.push("");

    if (group.open.length > 0) {
      lines.push(`### Open (${group.open.length})`);
      lines.push("");
      lines.push("| # | Title | Created |");
      lines.push("|---|-------|---------|");
      for (const issue of group.open) {
        lines.push(`| [#${issue.number}](${issue.url}) | ${issue.title} | ${issue.created} |`);
      }
      lines.push("");
    }

    if (group.closed.length > 0) {
      lines.push(`### Closed (${group.closed.length})`);
      lines.push("");
      lines.push("| # | Title | Created | Closed | Time-to-close |");
      lines.push("|---|-------|---------|--------|---------------|");
      for (const issue of group.closed) {
        lines.push(
          `| [#${issue.number}](${issue.url}) | ${issue.title} | ${issue.created} | ${issue.closed} | **${issue.timeToClose}** |`,
        );
      }
      lines.push("");
    }
  }

  lines.push("---");
  lines.push("");

  if (aggregate.overallAvgMs !== null) {
    lines.push(`| Overall avg (closed only) | ~${formatAverageDuration(aggregate.overallAvgMs)} |`);
  } else {
    lines.push("| Overall avg (closed only) | — |");
  }

  lines.push("");
  return lines.join("\n");
}

async function main() {
  const cli = parseCliArgs();

  if (!process.env.GITHUB_TOKEN) {
    console.warn("Warning: GITHUB_TOKEN is not set. GitHub API rate limits may apply.");
  }

  const octokit = new Octokit({
    auth: process.env.GITHUB_TOKEN,
  });

  const fetched = await fetchIssues({
    octokit,
    fullRepo: cli.fullRepo,
    owner: cli.owner,
    repo: cli.repo,
    since: cli.since,
  });

  const aggregate = aggregateIssues(fetched.issues);
  const report = renderReport({
    sinceDate: cli.sinceDate,
    today: new Date(),
    fullRepo: cli.fullRepo,
    query: fetched.query,
    totalCount: fetched.totalCount,
    truncated: fetched.totalCount > MAX_SEARCH_RESULTS,
    aggregate,
  });

  fs.writeFileSync(cli.outputPath, report, "utf8");
  console.log(`Report written to ${cli.outputPath}`);
  console.log(
    `Issues: ${aggregate.totalOpen + aggregate.totalClosed} total (${aggregate.totalOpen} open, ${aggregate.totalClosed} closed)`,
  );
}

main().catch((err) => {
  console.error("");
  console.error("Failed to generate issues report");
  console.error(err.message || err);

  process.exit(1);
});
