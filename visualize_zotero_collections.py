#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numba>=0.60",
#   "numpy>=1.26",
#   "plotly>=6.0",
#   "scikit-learn>=1.4",
#   "umap-learn>=0.5.8",
# ]
# ///
"""Map Zotero papers and recolor them through every parent collection."""

from __future__ import annotations

import argparse
import csv
import html
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import umap
from plotly.colors import qualitative
from sklearn.feature_extraction.text import TfidfVectorizer

MULTIPLE_TOPICS = "Multiple topics"
DIRECTLY_IN_ROOT = "Directly in root"
OUTSIDE_ROOT = "Outside selected parent"


def snapshot_database(source: Path, destination_dir: Path) -> Path:
    """Copy Zotero's database and live WAL files without modifying the library."""
    if not source.is_file():
        raise FileNotFoundError(f"Zotero database not found: {source}")

    snapshot = destination_dir / source.name
    shutil.copy2(source, snapshot)
    for suffix in ("-wal", "-shm"):
        sidecar = source.with_name(source.name + suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, snapshot.with_name(snapshot.name + suffix))
    return snapshot


def load_library(
    database: Path,
) -> tuple[
    set[str],
    dict[int, set[str]],
    dict[int, str],
    dict[int, str],
]:
    """Return collection paths, item memberships, titles, and searchable text."""
    query = """
        WITH RECURSIVE collection_paths(collectionID, path) AS (
            SELECT collectionID, collectionName
            FROM collections
            WHERE parentCollectionID IS NULL
            UNION ALL
            SELECT child.collectionID,
                   collection_paths.path || ' / ' || child.collectionName
            FROM collections AS child
            JOIN collection_paths
              ON child.parentCollectionID = collection_paths.collectionID
        )
        SELECT
            collection_paths.path,
            items.itemID,
            MAX(CASE WHEN fields.fieldName = 'title'
                     THEN itemDataValues.value END) AS title,
            MAX(CASE WHEN fields.fieldName = 'abstractNote'
                     THEN itemDataValues.value END) AS abstract
        FROM collection_paths
        LEFT JOIN collectionItems
          ON collectionItems.collectionID = collection_paths.collectionID
        LEFT JOIN items
          ON items.itemID = collectionItems.itemID
        LEFT JOIN deletedItems
          ON deletedItems.itemID = items.itemID
        LEFT JOIN itemTypes
          ON itemTypes.itemTypeID = items.itemTypeID
        LEFT JOIN itemData
          ON itemData.itemID = items.itemID
        LEFT JOIN fields
          ON fields.fieldID = itemData.fieldID
         AND fields.fieldName IN ('title', 'abstractNote')
        LEFT JOIN itemDataValues
          ON itemDataValues.valueID = itemData.valueID
        WHERE deletedItems.itemID IS NULL
          AND (
              items.itemID IS NULL
              OR itemTypes.typeName NOT IN ('attachment', 'note', 'annotation')
          )
        GROUP BY collection_paths.path, items.itemID
        ORDER BY collection_paths.path, items.itemID
    """

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(query).fetchall()
    finally:
        connection.close()

    collection_paths: set[str] = set()
    memberships: dict[int, set[str]] = defaultdict(set)
    titles: dict[int, str] = {}
    documents: dict[int, str] = {}
    for collection_path, item_id, title, abstract in rows:
        collection_paths.add(collection_path)
        if item_id is None:
            continue

        title = (title or "").strip()
        abstract = (abstract or "").strip()
        text = " ".join(part for part in (title, abstract) if part)
        if not text:
            continue

        memberships[item_id].add(collection_path)
        titles[item_id] = title or f"Zotero item {item_id}"
        documents[item_id] = text

    return collection_paths, memberships, titles, documents


def topic_for_item(collection_paths: set[str], root: str) -> str | None:
    """Derive an existing immediate-child topic without guessing conflicts."""
    prefix = root + " / "
    topics = {
        DIRECTLY_IN_ROOT
        if path == root
        else path[len(prefix) :].split(" / ", 1)[0]
        for path in collection_paths
        if path == root or path.startswith(prefix)
    }
    if not topics:
        return None
    if len(topics) > 1:
        return MULTIPLE_TOPICS
    return topics.pop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create one semantic map of Zotero papers with a selector that colors "
            "papers by the immediate children of every parent collection."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home() / "Zotero" / "zotero.sqlite",
        help="Path to zotero.sqlite (default: ~/Zotero/zotero.sqlite).",
    )
    parser.add_argument(
        "--root",
        default="03 Topics",
        help=(
            "Parent collection selected when the map opens. Every parent remains "
            "available from the map selector (default: '03 Topics')."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("zotero_topic_map.html"),
        help="Output interactive HTML map.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with tempfile.TemporaryDirectory(prefix="zotero-topic-map-") as temp_dir:
        snapshot = snapshot_database(args.db.expanduser(), Path(temp_dir))
        collection_paths, memberships, titles, documents = load_library(snapshot)

    parent_collections = sorted(
        {
            path.rsplit(" / ", 1)[0]
            for path in collection_paths
            if " / " in path
        },
        key=lambda path: (path.count(" / "), path.casefold()),
    )
    if args.root not in parent_collections:
        raise ValueError(
            f"Parent collection not found: {args.root!r}. "
            f"Available parent collections: {', '.join(parent_collections)}"
        )

    topics_by_parent = {
        parent: {
            item_id: topic_for_item(item_memberships, parent)
            for item_id, item_memberships in memberships.items()
        }
        for parent in parent_collections
    }
    item_ids = sorted(documents)
    if len(item_ids) < 3:
        raise ValueError("Fewer than three text-bearing collection items were found.")

    minimum_document_frequency = 2 if len(item_ids) >= 10 else 1
    item_vectors = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=minimum_document_frequency,
        max_df=0.95,
        max_features=30_000,
        sublinear_tf=True,
    ).fit_transform(documents[item_id] for item_id in item_ids)
    coordinates = umap.UMAP(
        n_components=2,
        n_neighbors=min(30, len(item_ids) - 1),
        min_dist=0.12,
        metric="cosine",
        random_state=42,
        n_jobs=1,
    ).fit_transform(item_vectors)

    figure = go.Figure()
    trace_ranges: dict[str, tuple[int, int]] = {}
    hover_data = {
        item_id: [
            html.escape(titles[item_id]),
            "<br>".join(
                html.escape(path) for path in sorted(memberships[item_id])
            ),
        ]
        for item_id in item_ids
    }
    for parent in parent_collections:
        trace_start = len(figure.data)
        topics = topics_by_parent[parent]
        regular_topics = sorted(
            topic
            for topic in set(topics.values())
            if topic not in {None, MULTIPLE_TOPICS, DIRECTLY_IN_ROOT}
        )
        color_by_topic = {
            topic: qualitative.Dark24[index % len(qualitative.Dark24)]
            for index, topic in enumerate(regular_topics)
        }
        color_by_topic[DIRECTLY_IN_ROOT] = "#777777"
        color_by_topic[MULTIPLE_TOPICS] = "#222222"
        color_by_topic[OUTSIDE_ROOT] = "#c8c8c8"
        plot_order = [OUTSIDE_ROOT] + regular_topics + [
            topic
            for topic in (DIRECTLY_IN_ROOT, MULTIPLE_TOPICS)
            if topic in topics.values()
        ]

        for topic in plot_order:
            indices = [
                index
                for index, item_id in enumerate(item_ids)
                if (
                    topics[item_id] is None
                    if topic == OUTSIDE_ROOT
                    else topics[item_id] == topic
                )
            ]
            custom_data = np.array(
                [hover_data[item_ids[index]] for index in indices],
                dtype=object,
            )
            figure.add_trace(
                go.Scatter(
                    x=coordinates[indices, 0],
                    y=coordinates[indices, 1],
                    mode="markers",
                    name=f"{topic} ({len(indices)})",
                    customdata=custom_data,
                    visible=parent == args.root,
                    marker={
                        "color": color_by_topic[topic],
                        "opacity": (
                            0.16
                            if topic == OUTSIDE_ROOT
                            else 0.95
                            if topic == MULTIPLE_TOPICS
                            else 0.72
                        ),
                        "size": (
                            4
                            if topic == OUTSIDE_ROOT
                            else 10
                            if topic == MULTIPLE_TOPICS
                            else 7
                        ),
                        "symbol": "x" if topic == MULTIPLE_TOPICS else "circle",
                    },
                    hovertemplate=(
                        "<b>%{customdata[0]}</b><br>"
                        f"Group: {html.escape(topic)}<br>"
                        "Collections:<br>%{customdata[1]}<extra></extra>"
                    ),
                )
            )
        trace_ranges[parent] = (trace_start, len(figure.data))

    parent_buttons = []
    for parent in parent_collections:
        start, end = trace_ranges[parent]
        parent_buttons.append(
            {
                "label": parent,
                "method": "update",
                "args": [
                    {
                        "visible": [
                            start <= index < end
                            for index in range(len(figure.data))
                        ]
                    },
                    {
                        "title.text": (
                            f"Individual Zotero papers — "
                            f"{html.escape(parent)}"
                        )
                    },
                ],
            }
        )

    figure.update_layout(
        title={
            "text": (
                f"Individual Zotero papers — {html.escape(args.root)}"
            ),
            "x": 0.01,
            "xanchor": "left",
        },
        template="plotly_white",
        hovermode="closest",
        legend={
            "title": {"text": "Immediate child (paper count)"},
            "itemsizing": "constant",
        },
        updatemenus=[
            {
                "buttons": parent_buttons,
                "direction": "down",
                "showactive": True,
                "active": parent_collections.index(args.root),
                "x": 1.0,
                "xanchor": "right",
                "y": 1.13,
                "yanchor": "top",
            }
        ],
        annotations=[
            {
                "text": "Parent collection:",
                "showarrow": False,
                "x": 0.67,
                "xanchor": "right",
                "xref": "paper",
                "y": 1.10,
                "yanchor": "middle",
                "yref": "paper",
            }
        ],
        margin={"l": 30, "r": 30, "t": 115, "b": 45},
        xaxis={
            "title": "UMAP 1",
            "showticklabels": False,
            "showgrid": False,
            "zeroline": False,
        },
        yaxis={
            "title": "UMAP 2",
            "showticklabels": False,
            "showgrid": False,
            "zeroline": False,
            "scaleanchor": "x",
            "scaleratio": 1,
        },
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(
        args.output,
        include_plotlyjs=True,
        full_html=True,
        config={
            "displaylogo": False,
            "responsive": True,
            "scrollZoom": True,
        },
    )

    csv_path = args.output.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "item_id",
                "title",
                "collection_paths",
                "umap_1",
                "umap_2",
                *[
                    f"immediate_child_under::{parent}"
                    for parent in parent_collections
                ],
            ]
        )
        for index, item_id in enumerate(item_ids):
            writer.writerow(
                [
                    item_id,
                    titles[item_id],
                    " | ".join(sorted(memberships[item_id])),
                    f"{coordinates[index, 0]:.8g}",
                    f"{coordinates[index, 1]:.8g}",
                    *[
                        topics_by_parent[parent][item_id] or ""
                        for parent in parent_collections
                    ],
                ]
            )

    selected_counts = Counter(
        topic or OUTSIDE_ROOT
        for topic in topics_by_parent[args.root].values()
    )
    print(
        f"Mapped {len(item_ids)} text-bearing papers across "
        f"{len(parent_collections)} selectable parent collections."
    )
    print(
        f"Initial view {args.root!r}: "
        + ", ".join(
            f"{topic}={count}"
            for topic, count in sorted(
                selected_counts.items(), key=lambda pair: (-pair[1], pair[0])
            )
        )
    )
    print(f"Wrote {args.output}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
