# Sample Telegram alert

What lands in your Telegram chat looks like this (Markdown-rendered):

> 🔔 **New role matches found**
>
> **🟢 Delhi NCR**
>
> **VP – Account Management, Life Insurance**
> 📍 Gurgaon, India | via Google Alerts — Life Insurance VP (Account Mgmt / Transformation)
> _Direct VP Account Management title in Life Insurance, based in Gurgaon — strong fit._
> 🔗 https://example.com/job/12345
>
> **🟡 India (other)**
>
> **Business Head — Travel & Hospitality**
> 📍 Mumbai, India | via Indeed India — Business Head, Travel/Hospitality (pan-India)
> _Business Head title in Travel matches target role 3._
> 🔗 https://example.com/job/67890
>
> **🔵 UAE**
>
> **AVP Transformation — Insurance**
> 📍 Dubai, UAE | via Google Alerts — Life Insurance VP (Account Mgmt / Transformation)
> _Transformation VP-equivalent role in Insurance, UAE market accepts international hires._
> 🔗 https://example.com/job/54321

If a run finds no new, relevant postings, the workflow sends nothing — the
`Dedupe & Filter Recent` and `Relevant Only` steps both short-circuit on empty
input, so you won't get "no new jobs today" noise every 6 hours.
