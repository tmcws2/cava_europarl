name: Test Bluesky Post

on:
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install atproto

      - name: Post test
        env:
          BLUESKY_PASSWORD: ${{ secrets.BLUESKY_PASSWORD }}
        run: python test_post.py
