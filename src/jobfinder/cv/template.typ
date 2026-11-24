// The CV and the cover letter, in the layout of Jake's Resume (github.com/jakegut/resume, MIT):
// one column, a centred name in small caps over one contact line, small-caps section headings
// over a rule, entries with the title and dates on one line and the company and place in
// italics below, small bullets.
//
// The same file lays out the posting itself (kind "job"), as stored, for the candidate to read.
//
// Everything comes in as JSON data (sys.inputs.data, written by src/jobfinder/cv/render.py).
// Strings are placed as text, never parsed as Typst markup, so no character in them can change
// the layout. ATS: real text with Unicode mapping, no ligatures, no hyphenation, no columns,
// tables, images or icons, the contact details in the body.

#let data = json(bytes(sys.inputs.data))

#set document(title: data.title, author: if "name" in data { data.name } else { () })
#set page(paper: data.paper, margin: (x: 0.5in, y: 0.5in))
#set text(font: "New Computer Modern", size: 10.5pt, lang: "en", ligatures: false, hyphenate: false)
#set par(justify: false, leading: 0.5em, spacing: 0.65em)
#set list(indent: 0.4em, body-indent: 0.5em, spacing: 0.45em)

#show heading.where(level: 1): it => block(
  above: 1.1em, below: 0.6em, width: 100%, stroke: (bottom: 0.5pt), inset: (bottom: 2.5pt),
  text(size: 12pt, weight: "regular", smallcaps(it.body)),
)

#let header() = align(center)[
  #text(size: 22pt, weight: "bold", smallcaps(data.name))
  #v(-0.4em)
  #text(size: 10pt, data.contact.map(c => if type(c) == dictionary { link(c.url, underline(c.text)) } else { c }).join(" | "))
]

#let entry(top-left, top-right, bottom-left, bottom-right) = block(above: 0.75em, below: 0.45em, grid(
  columns: (1fr, auto), row-gutter: 0.5em,
  strong(top-left), top-right,
  emph(text(size: 10pt, bottom-left)), emph(text(size: 10pt, bottom-right)),
))

#let bullets(items) = text(size: 10pt, list(..items))

#if data.kind == "cv" {
  header()

  if data.summary != "" [
    = Summary
    #data.summary
  ]

  let experience() = [
    = Experience
    #for job in data.experience {
      entry(job.title, job.dates, job.company, job.location)
      bullets(job.bullets)
    }
  ]
  let education() = [
    = Education
    #for edu in data.education {
      entry(edu.school, edu.dates, edu.degree, edu.detail)
    }
  ]

  if data.education_first { education(); experience() } else { experience() }

  if data.projects.len() > 0 [
    = Projects
    #for project in data.projects {
      block(above: 0.75em, below: 0.45em)[#strong(project.name)#if project.tech != "" [ | #emph(project.tech)]]
      bullets((project.description,))
    }
  ]

  [
    = Technical Skills
    #for group in data.skills {
      block(above: 0.45em, below: 0.45em, text(size: 10pt)[#strong(group.category + ":") #group.items.join(", ")])
    }
  ]

  if not data.education_first { education() }

  if data.certifications.len() > 0 [
    = Certifications
    #bullets(data.certifications)
  ]
  if data.publications.len() > 0 [
    = Publications
    #bullets(data.publications)
  ]
  if data.languages != "" [
    = Languages
    #text(size: 10pt, data.languages)
  ]
} else if data.kind == "job" [
  #align(center)[
    #text(size: 16pt, weight: "bold", data.title)
    #if data.company != "" [ \ #text(size: 12pt, data.company)]
  ]
  #v(0.4em)
  #for row in data.details {
    block(above: 0.35em, below: 0.35em, text(size: 10pt)[#strong(row.label + ":") #row.value])
  }
  #if data.warnings.len() > 0 [
    = Safety warnings
    #bullets(data.warnings)
  ]
  = Description
  #set par(spacing: 0.8em)
  #for paragraph in data.paragraphs [#paragraph #parbreak()]
] else [
  #set text(size: 11pt)
  #set par(spacing: 1.1em)
  #header()
  #v(1.2em)
  #data.date

  #if data.recipient != "" [#data.recipient #parbreak()]
  #strong(data.subject)

  #data.greeting

  #for paragraph in data.paragraphs [#paragraph #parbreak()]
  #data.closing \
  #v(0.6em)
  #data.name
]
