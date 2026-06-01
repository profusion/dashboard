from unittest.mock import Mock, patch

from django.test import override_settings

from kernelCI_app.queries.tree import (
    get_latest_tree,
    get_tree_details_data,
    get_tree_listing_data,
    get_tree_listing_data_by_checkout_id,
    get_tree_listing_fast,
)
from kernelCI_app.tests.unitTests.queries.conftest import (
    setup_mock_cursor,
    setup_mock_queryset,
)


class TestGetTreeListingData:
    @patch("kernelCI_app.queries.tree.dict_fetchall")
    @patch("kernelCI_app.queries.tree.connection")
    def test_get_tree_listing_data_success(self, mock_connection, mock_dict_fetchall):
        expected_result = [{"checkout_id": "checkout", "tree_name": "mainline"}]
        mock_dict_fetchall.return_value = expected_result
        setup_mock_cursor(mock_connection)

        result = get_tree_listing_data(origin="maestro", interval_in_days=7)

        assert result == expected_result

    @override_settings(DB_SCHEMA_REFACTOR_READ_PATH="runs")
    @patch("kernelCI_app.queries.tree.dict_fetchall")
    @patch("kernelCI_app.queries.tree.connection")
    def test_get_tree_listing_data_runs_read_path(
        self, mock_connection, mock_dict_fetchall
    ):
        expected_result = [{"checkout_id": "checkout", "tree_name": "mainline"}]
        mock_dict_fetchall.return_value = expected_result
        mock_cursor = setup_mock_cursor(mock_connection)

        result = get_tree_listing_data(origin="maestro", interval_in_days=7)

        query = mock_cursor.execute.call_args.args[0]
        params = mock_cursor.execute.call_args.args[1]
        assert result == expected_result
        assert "JOIN commits ON checkouts.commit_id = commits.id" in query
        assert "build_counts AS" in query
        assert "test_counts AS" in query
        assert "JOIN test_runs AS tests" in query
        assert "tests.is_boot" in query
        assert "builds.kci_id NOT LIKE 'maestro:dummy_%%'" in query
        assert params["origin_param"] == "maestro"


class TestGetTreeListingFast:
    @patch("kernelCI_app.queries.tree.get_query_time_interval")
    @patch("kernelCI_app.queries.tree.Checkouts")
    def test_get_tree_listing_fast_with_origin(
        self, mock_checkouts_model, mock_get_interval
    ):
        mock_get_interval.return_value.timestamp.return_value = 1704067200.0
        mock_checkouts_model.objects.raw.return_value = [Mock(id="checkout")]

        result = get_tree_listing_fast(origin="maestro", interval={"days": 7})

        assert len(result) == 1

    @patch("kernelCI_app.queries.tree.get_query_time_interval")
    @patch("kernelCI_app.queries.tree.Checkouts")
    def test_get_tree_listing_fast_without_origin(
        self, mock_checkouts_model, mock_get_interval
    ):
        mock_get_interval.return_value.timestamp.return_value = 1704067200.0
        mock_checkouts_model.objects.raw.return_value = []

        result = get_tree_listing_fast(origin=None, interval={"days": 7})

        assert result == []

    @override_settings(DB_SCHEMA_REFACTOR_READ_PATH="commits")
    @patch("kernelCI_app.queries.tree.get_query_time_interval")
    @patch("kernelCI_app.queries.tree.Checkouts")
    def test_get_tree_listing_fast_commits_read_path(
        self, mock_checkouts_model, mock_get_interval
    ):
        mock_get_interval.return_value.timestamp.return_value = 1704067200.0
        mock_checkouts_model.objects.raw.return_value = [Mock(id="checkout")]

        result = get_tree_listing_fast(origin="maestro", interval={"days": 7})

        query = mock_checkouts_model.objects.raw.call_args.args[0]
        params = mock_checkouts_model.objects.raw.call_args.args[1]
        assert len(result) == 1
        assert "JOIN commits ON checkouts.commit_id = commits.id" in query
        assert params["origin"] == "maestro"


class TestGetTreeListingDataByCheckoutId:
    @patch("kernelCI_app.queries.tree.dict_fetchall")
    @patch("kernelCI_app.queries.tree.connection")
    def test_get_tree_listing_data_by_checkout_id_success(
        self, mock_connection, mock_dict_fetchall
    ):
        expected_result = [{"id": "checkout_1", "tree_name": "mainline"}]
        mock_dict_fetchall.return_value = expected_result
        setup_mock_cursor(mock_connection)

        result = get_tree_listing_data_by_checkout_id(
            checkout_ids=["checkout_1", "checkout_2"]
        )

        assert result == expected_result

    @override_settings(DB_SCHEMA_REFACTOR_READ_PATH="runs")
    @patch("kernelCI_app.queries.tree.dict_fetchall")
    @patch("kernelCI_app.queries.tree.connection")
    def test_get_tree_listing_data_by_checkout_id_runs_read_path(
        self, mock_connection, mock_dict_fetchall
    ):
        expected_result = [{"id": "checkout_1", "tree_name": "mainline"}]
        mock_dict_fetchall.return_value = expected_result
        mock_cursor = setup_mock_cursor(mock_connection)

        result = get_tree_listing_data_by_checkout_id(
            checkout_ids=["checkout_1", "checkout_2"]
        )

        query = mock_cursor.execute.call_args.args[0]
        params = mock_cursor.execute.call_args.args[1]
        assert result == expected_result
        assert "commits ON checkouts.commit_id = commits.id" in query
        assert "build_runs AS builds" in query
        assert "test_runs AS tests" in query
        assert "tests.is_boot" in query
        assert "builds.kci_id NOT LIKE 'maestro:dummy_%%'" in query
        assert params == ["checkout_1", "checkout_2"]


class TestGetTreeDetailsData:
    @patch("kernelCI_app.queries.tree.get_query_cache")
    def test_get_tree_details_data_from_cache(self, mock_get_cache):
        cached_data = [("row1", "row2")]
        mock_get_cache.return_value = cached_data

        result = get_tree_details_data(
            origin_param="maestro",
            git_url_param="https://my_url.com",
            git_branch_param="master",
            commit_hash="abc123",
        )

        assert result == cached_data

    @patch("kernelCI_app.queries.tree.get_query_cache")
    @patch("kernelCI_app.queries.tree.set_query_cache")
    @patch("kernelCI_app.queries.tree.create_checkouts_where_clauses")
    @patch("kernelCI_app.queries.tree.connection")
    def test_get_tree_details_data_from_database(
        self,
        mock_connection,
        mock_create_clauses,
        mock_set_cache,
        mock_get_cache,
    ):
        expected_data = [("row1", "row2")]
        mock_get_cache.return_value = None
        mock_create_clauses.return_value = {
            "git_branch_clause": "git_repository_branch = %(git_branch_param)s",
            "tree_name_clause": "",
            "git_url_clause": "git_repository_url = %(git_url_param)s",
        }
        mock_cursor = setup_mock_cursor(mock_connection)
        mock_cursor.fetchall.return_value = expected_data

        result = get_tree_details_data(
            origin_param="maestro",
            git_url_param="https://my_url.com",
            git_branch_param="master",
            commit_hash="abc123",
        )

        assert result == expected_data
        mock_set_cache.assert_called_once()


class TestGetLatestTree:
    @override_settings(DB_SCHEMA_REFACTOR_READ_PATH="")
    @patch("kernelCI_app.queries.tree.Checkouts")
    def test_get_latest_tree_success(self, mock_checkouts_model):
        expected_result = {"git_commit_hash": "abc123", "tree_name": "mainline"}
        setup_mock_queryset(mock_checkouts_model, expected_result)

        result = get_latest_tree(
            tree_name="mainline",
            git_branch="master",
            origin="maestro",
            git_commit_hash="abc123",
        )

        assert result == expected_result

    @override_settings(DB_SCHEMA_REFACTOR_READ_PATH="")
    @patch("kernelCI_app.queries.tree.Checkouts")
    def test_get_latest_tree_not_found(self, mock_checkouts_model):
        setup_mock_queryset(mock_checkouts_model, None)

        result = get_latest_tree(
            tree_name="nonexistent_tree",
            git_branch="master",
            origin="maestro",
            git_commit_hash=None,
        )

        assert result is None

    @override_settings(DB_SCHEMA_REFACTOR_READ_PATH="")
    @patch("kernelCI_app.queries.tree.Checkouts")
    def test_get_latest_tree_not_found_with_commit_hash(self, mock_checkouts_model):
        setup_mock_queryset(mock_checkouts_model, None)

        result = get_latest_tree(
            tree_name="nonexistent_tree",
            git_branch="master",
            origin="maestro",
            git_commit_hash="nonexistent_hash",
        )

        assert result is None

    @override_settings(DB_SCHEMA_REFACTOR_READ_PATH="commits")
    @patch("kernelCI_app.queries.tree.Checkouts")
    def test_get_latest_tree_commits_read_path(self, mock_checkouts_model):
        expected_result = {"git_commit_hash": "abc123", "tree_name": "mainline"}
        mock_queryset = setup_mock_queryset(mock_checkouts_model, expected_result)
        mock_checkouts_model.objects.filter.return_value = mock_queryset

        result = get_latest_tree(
            tree_name="mainline",
            git_branch="master",
            origin="maestro",
            git_commit_hash="abc123",
        )

        assert result == expected_result
        mock_checkouts_model.objects.filter.assert_called_once_with(
            origin="maestro",
            commit__isnull=False,
            commit__git_repository_branch="master",
            commit__tree_name="mainline",
        )
        mock_queryset.values.assert_called_once()
