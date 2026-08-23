# mcp-browser only · base plus a real Chromium.
#
# Split out because Playwright's browser download is roughly 1GB and every
# other service in the crew was carrying it for nothing.

FROM crew-base:dev

# Playwright defaults to ~/.cache, so installing as root puts the browsers
# under /root -- which the `crew` user this pod runs as cannot read. The pod
# then fails at startup with "please run `playwright install`", which is a
# confusing way to say "wrong home directory". Install somewhere neutral and
# tell both users where it is.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

USER root
RUN playwright install --with-deps chromium && \
    chmod -R a+rX /opt/playwright
USER crew

CMD ["uvicorn", "mocks.mcp_browser.main:app", "--host", "0.0.0.0", "--port", "9004"]
