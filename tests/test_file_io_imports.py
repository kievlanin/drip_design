def test_file_io_impl_exports_load_project_without_missing_imports():
    from main_app.io import file_io_impl

    assert callable(file_io_impl.load_project)
