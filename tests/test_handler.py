from lambda_fuction import lambda_handler

def test_lambda_handler():
    # Mock event and context
    mock_event = {"key": "value"}  # Replace with actual event structure if needed
    mock_context = {}

    # Call the lambda_handler function
    try:
        lambda_handler(mock_event, mock_context)
        assert True  # If no exception is raised, the test passes
    except Exception as e:
        assert False, f"lambda_handler raised an exception: {e}"