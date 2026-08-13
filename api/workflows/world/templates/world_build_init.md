You are generating the starting location for a text adventure.

## World
{world_description}

## Placement
This is the first location in the world. It will be placed at [0, 0] with a footprint of 1-4 cells (your choice).

Use the create_location tool to generate exactly one location consistent with the world tone. Add exits for directions that should be passable — these do not need a destination yet, they just signal to the player that the way is passable. Always give the exits a description - they may be closed doors so the player can't see where they go yet. Directions with no exit are blocked (walls, cliffs, etc., as fits the world). The actual locations beyond these exits will be generated later as the player explores them.

## Rules
- You do not have to create all the characters or items or reveal everything about the world in the first lcoation. Save some suprises for later.