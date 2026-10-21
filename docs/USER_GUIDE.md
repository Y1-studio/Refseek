# Artist's guide


Find pictures in your own collection by describing them or showing the app
an example. You can look for a subject, a drawing style, a pose, a colour
palette, or an overall resemblance, even when the filenames are unhelpful.

The results are files you already have. Your original artwork stays where
you saved it. Search runs on your computer.

New here? Follow sections 1, 2 and 3 first. The other sections explain the
controls when you need them.


## 1. Open The App

The program is called SemanticSearch-Native.exe.

An ".exe" file is a Windows application: double-click it (two quick clicks)
to open the program.
Windows may hide the ".exe" ending, so you might see just
"SemanticSearch-Native".

If you received a ZIP file, extract it first: right-click the ZIP, choose
"Extract All", and open the folder Windows creates.

Keep the app in its own folder somewhere convenient, such as Documents.
For example, make a folder called "Art Search" and put the application there.
To make a folder, open Documents, right-click an empty space, choose New,
then Folder, and type its name. Drag the application file into that folder.
It needs room to save its working files, so leave several gigabytes free.

Open the application, then wait. A small opening window appears,
followed by the main window with a loading message. The first opening takes
longer because the app prepares the files it needs. When loading finishes,
you can begin.

The left side of the window has three places:
  Search   - find and browse your files.  
  Library  - choose which folders the app knows about.  
  Settings - change the size, appearance and layout of the controls.  


## 2. Add Your First Image Folder

[![Library: choose a folder, start indexing, and watch the progress.](screenshots/library.png)](screenshots/library.png)

*Library: choose a folder, start indexing, and watch the progress. Click the image for a larger view.*

Before searching, show the app where your pictures are.

1) Click "Library" on the left.
2) Click "Browse..." and choose a folder containing your reference images.
   It also looks inside the folders within that folder.  
3) Make sure "image" is checked. Uncheck the other file types if you only
   want to prepare pictures.  
4) Click "Add & index".
5) Wait for it to finish preparing your collection.

"Indexing" means making a searchable catalogue of your files. The first run
takes longer because the app looks at each image. It saves its search notes
so that unchanged files can be reused next time.

Your files keep their original names and locations. You can add more folders
by repeating these steps.

To try the app before adding a huge collection, start with a smaller folder
you know well. This makes it easier to recognise useful results.

What the progress numbers mean:
  files/s processed - how many files it is checking each second.  
  new files/s       - how quickly it is preparing files that need new work.  
  embedded          - files for which it has created new search information.  
  cached            - files whose existing search information was reused.  
  skipped           - files it could not prepare.  
  Elapsed           - time already spent.  
  ETA               - an estimate of the time remaining; this can change.  

You can search while indexing continues. Searches use the work already saved.
Newly prepared pictures appear in saved batches. Run the search again to see
the latest additions.

"Cancel indexing" stops the job after its current work can finish safely.
Completed work is kept. You can update the folder later to continue.


## 3. Make A Simple Text Search

[![Search: type a description, choose an optional reference, then search your library.](screenshots/search.png)](screenshots/search.png)

*Search: type a description, choose an optional reference, then search your library. Click the image for a larger view.*

1) Click "Search" on the left.
2) In "What are you looking for?", describe the picture you want.
3) Click "Search library".
4) Click a result to see a larger preview.

Try descriptions such as:
  a person sitting on the floor  
  delicate botanical ink drawings  
  a forest path in warm evening light  
  a character seen from behind  

Start with a clear, short description. Add a useful detail if the results are
too broad. You can rewrite the description and search again.

If an old reference image is still selected, click "Clear" beneath it to
return to a text-only search. This clears the search example, not the file.


## 4. Search With An Example Image

A "reference image" is the example the app compares your collection against.
It can come from inside or outside your indexed folders.

1) Click "Choose an image..." and select a picture.
   Alternatively, drag an image file from a Windows folder into the app.  
2) Choose what you want to match, using the buttons below the search text.
3) Click "Search library".

These matching buttons appear after you select a reference image:

  Similarity  
  Look for an overall resemblance: a mixture of subject, appearance and layout.  
  Start here when you want "more pictures like this".  

  Subject  
  Concentrate on what the image contains.  
  Example: use a rabbit photograph to look for other rabbits, including art  
  in different media.  

  Style  
  Concentrate on how an image looks: drawing, painting or rendering style.  
  Example: use an ink drawing of a beetle to look for similarly drawn plants  
  or other subjects.  

  Pose  
  Concentrate on the position of a body.  
  Example: look for other people sitting or raising their arms.  

  Viewpoint  
  Concentrate on the viewing angle, such as a side view or a view from above.  

  Composition  
  Concentrate on the arrangement of shapes and subjects within the frame.  

  Color  
  Concentrate on the colours and palette.  

  Lighting  
  Concentrate on light, shadow and the way a scene is lit.  

These are suggestions rather than exact rules. Style and pose matching can
be imperfect, and a subject or background can still influence the results.
The app looks at the whole reference image; you do not need to crop it.

For Style, leave "Contrastive style adapter" selected to start. It is the
default style-search method. "Current style adapter" and "Existing aspect
method" are alternative ways of ranking the same collection. You can compare
them with the same reference.

"Aspect strength" controls how much attention goes to your chosen feature.
Increase it to emphasise that feature. Reduce it to bring back more of the
overall resemblance. Click "Search library" after changing it.

Use "Change..." to choose a different reference, or "Clear" to remove it.


## 5. Combine An Image With Words

Choose a reference image, then also enter a description.

For example:
  Reference: a painting whose look you like.  
  Text: "a house beside a lake".  

Choose a matching feature, such as Style, then search.

"Image weight" controls the balance:
  Toward 0 - give the words more influence.  
  Toward 1 - give the reference image more influence.  

Start near the middle and adjust one control at a time. Search again after
each change so you can see what it did.


## 6. Add More Than One Search Idea

Click "+ Want" to add another description box. Use "Remove" beside a box to
remove that extra idea.

Example:
  First box: a forest path  
  Want 2:    warm sunset light  

For text-only searches, choose:
  Match all - combine the ideas into one search direction.  
  Match any - let a strong match to any one of the ideas rank highly.  

"Match all" is not a strict checklist: a result may fit one part better than
another. With a reference image, the text ideas are always combined first,
then balanced against the image using Image weight.

Click "Search library" to apply your descriptions.


## 7. Refine The Results

[![Refine: choose a negative image, exclude concepts, or select folders.](screenshots/refine.png)](screenshots/refine.png)

*Refine: choose a negative image, exclude concepts, or select folders. Click the image for a larger view.*

Click "Refine results & search scope" to reveal extra controls.
If a negative image is already selected, the heading says it is active.

### Negative image
A negative image is a counter-example: "less like this, please".

1) Open Refine.
2) Click "Choose negative image...".
3) Select the unwanted example.
4) Click "Search library" again.

The app reduces the ranking of results that resemble that image. "Negative
image weight" controls how strongly it does this. Start with the default.

This compares the whole counter-example. It cannot isolate one unwanted
object within it. It can also reduce matches you otherwise like, so try
"Clear negative image" if the results become less useful.

You can use a negative image with a text search or an image search.

### Ignore these concepts
Type unwanted concepts, one per line, such as:
  text  
  watermark  

"Text exclusion weight" controls how strongly these ideas push results down.
Clear the words to stop using those exclusions.

### File types
The checkboxes choose which kinds of files can appear in results.
For an artwork collection, "image" is the main one you need.

### Folders
These checkboxes choose where to search:
  No folders checked - search all folders in your library.  
  One folder checked - search that folder.  
  Several checked    - search those selected folders.  

A folder's displayed address, such as H:\draw, is its location on your
computer. You can select it with its checkbox without typing the address.

Search again after changing Refine controls.


## 8. Browse And Reuse Results

[![Viewer: use Previous/Next or the arrow keys to browse pictures.](screenshots/viewer.png)](screenshots/viewer.png)

*Viewer: use Previous/Next or the arrow keys to browse pictures. Click the image for a larger view.*

Click a picture to open its large preview.

  Previous / Next - move through pictures.  
  Left or Up key  - previous picture.  
  Right or Down   - next picture.  
  Esc             - close the preview.  

The preview stays the same size and position as you move between images.

Inside the preview:
  Use reference - search again using this picture as the example.  
  Use negative  - put this picture into Refine as the counter-example.  
  Open original - open the original file with its usual Windows application.  

On a result card:
  Use image - use that picture as a reference and search again.  
  Show file - reveal the original file in its Windows folder.  

Next and Previous near the page number move between pages of results.

A similarity percentage helps rank results. It is not a percentage chance
that the result is correct. Exclusions can lower a result's score.

When beginning a different search, check the words, extra Want boxes,
reference image and Refine settings. Previous choices stay active until you
change or clear them.


## 9. Keep Your Library Up To Date

After adding, changing or deleting pictures in a source folder:

1) Open "Library".
2) Find that folder.
3) Click "Update this folder".

Use "Update all folders" to check every folder already in the library.
The app reuses unchanged files, so updates often take less work than the
first indexing run. It checks for changes when you ask it to update.

"Open folder" opens that source folder in Windows.

"Remove from library" removes the folder from the app's searchable catalogue.
Your original files remain on your drive. You can add the folder again later.

Keep an external drive connected when searching pictures stored on it.
If you move or rename a source folder, add its new location and index it.
Once that works, remove the old location from the library.


## 10. Make The Window Comfortable To Use

[![Settings: change interface scale, text size, theme and result layout.](screenshots/settings.png)](screenshots/settings.png)

*Settings: change interface scale, text size, theme and result layout. Click the image for a larger view.*

Open "Settings".

  UI scale  
  Makes the entire interface larger or smaller: text, buttons and controls.  
  Try this first if everything feels too small.  

  Text size  
  Changes the size of the words.  

  Theme  
  Choose Dark, Light, or Follow Windows to match your computer's appearance.  

  Compact spacing and buttons  
  Fits controls closer together. Turn it off if you prefer more breathing room.  

  Preferred card width  
  Changes how wide each result's small display box is.  

  Thumbnail height  
  Changes the height of the small picture previews.  

  Results per page  
  Chooses how many results appear before you use the next page.  

  Show similarity scores and penalties  
  Shows the ranking numbers and the effect of exclusions.  

  Show full file paths on result cards  
  Shows each file's folder address beneath its name.  

Appearance changes happen immediately and save automatically. Scroll down
if larger controls extend below the visible part of the window.

"Reset appearance & layout" restores the default display settings.
It keeps your library.

Keyboard shortcuts:
  Ctrl and +   - make the interface larger.  
  Ctrl and -   - make it smaller.  
  Ctrl and 0   - restore the normal scale.  
  Ctrl + Enter - run a search.  

In "Local model", leave "Backend for the next launch" on Auto to start.
This chooses which part of your computer does the processing. CUDA uses a
compatible NVIDIA graphics card; Vulkan uses a compatible graphics device;
CPU uses the main processor. These are advanced choices, and any change
there takes effect after you close and reopen the app.


## 11. Saving, Moving And Updating The App

The app creates a folder called "data" beside the application. It holds your
search catalogue, settings and the working files the app needs.

Keep that data folder. If you move or back up the app, copy its whole folder,
including data. Your original image folders also need to remain available.

When receiving an updated SemanticSearch-Native.exe:
1) Close the running app and let it finish closing.
2) Replace the old application file in the same folder.
3) Keep the existing data folder.
4) Open the new application.

To close normally, click the X at the top right. If it is doing work, allow
it a moment to finish closing safely.


## 12. If Something Does Not Work

"I have no results."
  Add a folder in Library and let indexing prepare some pictures.  
  Check that the folder is connected and contains supported images.  
  In Search, clear unwanted folder restrictions and check "image" in Refine.  
  Try a simpler description.  

"The results are strange."
  Check for an old reference image, extra Want boxes or active exclusions.  
  Clear the negative image and ignored concepts, then try Similarity.  
  Change one control at a time so you can see its effect.  

"My new pictures are missing."
  Use "Update this folder" in Library, then run your search again.  
  If indexing is still running, some new pictures may not be saved yet.  

"A picture will not load."
  Common supported formats include JPG/JPEG, PNG, WebP, GIF, BMP and TIFF.  
  For layered drawing files, such as Photoshop or Clip Studio documents,  
  export a PNG or JPEG copy and index the folder containing those copies.  
  A file that is incomplete or damaged may still be unreadable.  

"It says the library is already open."
  Find the existing app window and use it, or close it before opening another.  

"The app cannot save its files."
  Keep its folder somewhere you can save files, such as Documents, and check  
  that the drive has free space.  

"I still get an error."
  Copy the exact message or take a screenshot. Note what you clicked and  
  which image or folder you were using, then send that information to the  
  person helping you with the app. "Open latest indexing details" in Library  
  can provide more information about skipped files.  


Technical build notes are in [BUILDING.md](NATIVE_BUILDING.md). You can use the app without reading them.

