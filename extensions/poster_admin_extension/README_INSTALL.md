# Poster Admin — Google Beside Me

This add-on is for you, the admin, on your own computer. It is not for workers.

It keeps ONE Google Images tab showing the place you are looking at in the
image zoom. When you move to the next picture, the same Google tab changes
to that place by itself. You never press CHECK GOOGLE twice, and no pile of
tabs builds up.

## Why it has to be an add-on

The website tried to do this on its own in v234 and could not. Google's
pages cut the link back to whichever page opened them, so the site lost
track of the Google tab the moment Google loaded. An add-on is allowed to
steer a tab, so it does the steering.

## Getting it

Sign in to the site as admin, open Worker Images, and press
**⬇ GOOGLE ADD-ON** in the bar at the top. Unzip the file you get. That
gives you the folder `poster_admin_extension`.

The copy the site hands out lives in the site's own code, in
`extensions/poster_admin_extension`, so every deploy carries the newest
version. Change the add-on THERE, never in a loose copy elsewhere.

## Installing it (once, in Chrome on your computer)

1. Type `chrome://extensions` in Chrome's address bar and press Enter.
2. Turn on **Developer mode**. The switch is at the top right.
3. Press **Load unpacked**.
4. Choose the unzipped folder `poster_admin_extension`.
5. "Poster Admin — Google Beside Me" appears in the list, switched on.
6. Reload any poster-site tab you already had open (press F5), so the
   add-on can join it.

## Using it

1. Open Worker Images or Changes Requested, and click a picture to zoom.
2. Press **CHECK GOOGLE** once. Google opens in a new tab right next to the
   site tab.
3. Put the two tabs side by side with Chrome's split view.
4. Click back on the site side. Now use the arrow keys, NEXT or FLAG as
   usual. The Google side follows every picture on its own.

Good to know:

- The Google tab is never brought to the front, so your keyboard stays on
  the site and the arrow keys keep working.
- Moving to another picture of the same place does not search Google again.
- If you close the Google tab, stepping does nothing to Google until you
  press CHECK GOOGLE again, which opens a fresh one.
- Without the add-on, CHECK GOOGLE still works the old way: one new tab per
  press.
- After updating the add-on, reload the site tab (F5). If you forget, the
  site shows a message asking you to.
