# Infor Supply Management

The tenant is the complete public Supplier Portal page URL, including `csk.SupplierGroup`.
Copy the buyer's URL, such as `/fsm/SupplyManagementSupplier/page/XiSupplyManagementSupplierPage?csk.SupplierGroup=LUMA`.
The spider derives the application root, supplier group, and home panel from that URL. It reads only
that page's HomeEvents list and follows the server's next-page URLs, preserving its scope.

Details, attachments, required questions (which can also contain documents), contacts, amendments,
lines, and public Q and A use Landmark JSON endpoints. Related lists are paginated independently.
Document URLs use the record's symbolic key and document field. No account is needed to read or download.
Landmark's 16-digit timestamps are UTC; the UI renders them in the visitor's browser time zone.
Idaho's event 1412 explicitly confirms 23:00 UTC equals 17:00 Mountain on October 28, 2026.

Documents occur in three places on the Idaho portal: the Attachments tab, the Questions tab,
and the Q and A Forum (both question and answer files). For example, event 1433 has 15
attachments, one required-question document, and 20 forum documents across 22 forum records.
Its forum uses 10 rows per page in the UI; requesting 20 is supported, with the server providing
`pagingUrls.nextPageUrl` for the final two. Fixtures include this pagination case.

Verification on October 22, 2026: the supplied Idaho home page showed two pages of 10 events,
and the live crawl returned PASS with 20 listings. Events 1412 and 1433 matched the UI titles,
deadlines, and 8/36 file links respectively. Plain HTTP downloads without a login returned real
DOCX/PDF bytes for an event attachment, a required-question attachment, and a forum attachment.
